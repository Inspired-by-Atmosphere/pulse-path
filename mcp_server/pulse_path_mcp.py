#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pulse-path MCP server — stdio JSON-RPC 2.0, standard library only.

Exposes the repository's read-only health / lint engines as MCP tools:

    pp_guard_check   -> skills/memory-pointer-system/scripts/mem_guard.py --check --verbose
    pp_scan_links    -> skills/pulse-path/scripts/scan_links.py
    pp_l2_coverage   -> skills/memory-pointer-system/scripts/check_l2_coverage.py
    pp_secrets_scan  -> scripts/check_secrets.py --json
    pp_search        -> skills/memory-pointer-system/scripts/pp_index.py --search (FTS5)

TRANSPORT / FRAMING
    MCP stdio transport, newline-delimited JSON: one UTF-8 JSON-RPC message per
    line, messages never contain a raw newline. That is what this server WRITES.
    The reader also accepts the LSP-style ``Content-Length: N`` header framing as
    a fallback (detected per message), so a client that speaks the older header
    form still works. Nothing but JSON-RPC frames is ever written to stdout;
    diagnostics go to stderr.

ZERO DEPENDENCIES
    No ``mcp``, no ``fastmcp``, no ``pydantic`` — just subprocess + json.

READ-ONLY
    Every engine is invoked in inspect-only mode (mem_guard always gets
    ``--check``), and this server writes no files. ``mem_guard.py`` itself
    appends its own audit report to ``$HERMES_HOME/reports/mem_guard-report.md``
    (engine behaviour, unchanged here) — see README "Limits".

USAGE
    python mcp_server/pulse_path_mcp.py              # speak JSON-RPC on stdio
    python mcp_server/pulse_path_mcp.py --help       # human help (no server)
    python mcp_server/pulse_path_mcp.py --print-tools  # dump tool schemas
    python mcp_server/pulse_path_mcp.py --version

ENVIRONMENT
    PULSE_PATH_MCP_TIMEOUT   per-engine timeout in seconds (default 120, max 600)
    Every other variable (HERMES_HOME, MEM_GUARD_*, SKILLS_ROOT, ...) is passed
    through to the engines untouched, so a client can pin the memory store via
    the ``env`` block of its server config.
"""

import ast
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

SERVER_NAME = "pulse-path"
SERVER_VERSION = "0.1.0"

# Newest first: the server answers in the client's version when it knows it,
# otherwise it answers with the newest version it implements.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05", "2024-10-07")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 600.0

# Resolve the repo root from this file, so the server starts from any cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent

ENGINE_PATHS = {
    "guard": REPO_ROOT / "skills" / "memory-pointer-system" / "scripts" / "mem_guard.py",
    "scan_links": REPO_ROOT / "skills" / "pulse-path" / "scripts" / "scan_links.py",
    "l2_coverage": REPO_ROOT / "skills" / "memory-pointer-system" / "scripts" / "check_l2_coverage.py",
    "secrets": REPO_ROOT / "scripts" / "check_secrets.py",
    "index": REPO_ROOT / "skills" / "memory-pointer-system" / "scripts" / "pp_index.py",
}

ENGINE_LABELS = {
    "guard": "skills/memory-pointer-system/scripts/mem_guard.py --check --verbose",
    "scan_links": "skills/pulse-path/scripts/scan_links.py",
    "l2_coverage": "skills/memory-pointer-system/scripts/check_l2_coverage.py",
    "secrets": "scripts/check_secrets.py --json",
    "index": "skills/memory-pointer-system/scripts/pp_index.py",
}


def log(msg):
    """Diagnostics go to stderr — stdout carries JSON-RPC frames only."""
    sys.stderr.write("[pulse-path-mcp] %s\n" % msg)
    sys.stderr.flush()


# ─────────────────────────────── framing ────────────────────────────────


def _decode_json(chunk):
    """Decode one frame's bytes; raise ValueError when it is not JSON."""
    return json.loads(chunk.decode("utf-8"))


def _read_exact(stream, size):
    buf = b""
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            raise ValueError("stream closed mid-frame (%d/%d bytes)" % (len(buf), size))
        buf += chunk
    return buf


def iter_messages(stream):
    """Yield decoded JSON-RPC messages from a binary stream.

    Primary framing: newline-delimited JSON (MCP stdio spec).
    Fallback: ``Content-Length: N`` headers followed by a blank line, then N raw
    bytes (LSP-style). The choice is made per message by peeking at the line.
    """
    while True:
        line = stream.readline()
        if not line:
            return
        text = line.strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered.startswith(b"content-length:"):
            try:
                size = int(text.split(b":", 1)[1].strip())
            except (IndexError, ValueError):
                raise ValueError("malformed Content-Length header: %r" % text[:80])
            # Drain any further headers up to the blank line.
            while True:
                header = stream.readline()
                if not header or not header.strip():
                    break
            yield _decode_json(_read_exact(stream, size))
            continue
        if lowered.startswith(b"content-type:"):
            # Header-only frame form; keep draining until blank line.
            continue
        yield _decode_json(text)


def write_message(stream, message):
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(payload + b"\n")
    stream.flush()


# ──────────────────────────── engine plumbing ───────────────────────────


def _engine_timeout(override=None):
    if override is not None:
        try:
            value = float(override)
        except (TypeError, ValueError):
            raise ValueError("timeout_s must be a number")
        if value <= 0:
            raise ValueError("timeout_s must be > 0")
        return min(value, MAX_TIMEOUT)
    raw = os.environ.get("PULSE_PATH_MCP_TIMEOUT")
    if raw:
        try:
            return max(1.0, min(float(raw), MAX_TIMEOUT))
        except ValueError:
            log("ignoring non-numeric PULSE_PATH_MCP_TIMEOUT=%r" % raw)
    return DEFAULT_TIMEOUT


def _tail(text, limit=2000):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def run_engine(engine_key, argv, env_extra=None, timeout_s=None):
    """Run one engine as a child process. Never raises for engine-side problems.

    Returns a dict with ``ok`` True on a completed run, else ok False plus a
    ``kind`` of ``script_missing`` / ``timeout`` / ``launch_failed``.
    """
    script = ENGINE_PATHS[engine_key]
    if not script.is_file():
        return {
            "ok": False,
            "kind": "script_missing",
            "message": "engine script not found: %s" % script,
        }

    python = sys.executable or "python3"
    cmd = [python, str(script)] + [str(a) for a in argv]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONSTARTUP", None)
    if env_extra:
        for key, value in env_extra.items():
            if value is None:
                continue
            env[str(key)] = str(value)

    timeout = _engine_timeout(timeout_s)
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,   # never let the engine eat our JSON-RPC stdin
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "kind": "timeout",
            "message": "engine exceeded %.1fs: %s" % (timeout, script.name),
            "timeout_s": timeout,
            "duration_ms": int((time.time() - started) * 1000),
            "argv": argv,
        }
    except OSError as exc:
        return {
            "ok": False,
            "kind": "launch_failed",
            "message": "cannot launch %s: %s" % (python, exc),
            "duration_ms": int((time.time() - started) * 1000),
        }

    return {
        "ok": True,
        "returncode": proc.returncode,
        "stdout": proc.stdout.decode("utf-8", "replace"),
        "stderr": proc.stderr.decode("utf-8", "replace"),
        "duration_ms": int((time.time() - started) * 1000),
        "timeout_s": timeout,
        "argv": argv,
    }


def engine_failure_payload(engine_key, run):
    payload = {
        "ok": False,
        "status": "engine_error",
        "engine": ENGINE_LABELS[engine_key],
        "error": {"kind": run.get("kind", "engine_error"),
                  "message": run.get("message", "engine failed")},
    }
    if run.get("returncode") is not None:
        payload["error"]["exit_code"] = run["returncode"]
    if run.get("stderr"):
        payload["error"]["stderr_tail"] = _tail(run["stderr"])
    if run.get("stdout"):
        payload["error"]["stdout_tail"] = _tail(run["stdout"])
    if run.get("duration_ms") is not None:
        payload["duration_ms"] = run["duration_ms"]
    return payload


def bad_output_payload(engine_key, message, run, **extra):
    payload = {
        "ok": False,
        "status": "bad_output",
        "engine": ENGINE_LABELS[engine_key],
        "error": {"kind": "stdout_not_parseable", "message": message,
                  "exit_code": run.get("returncode")},
        "stdout_tail": _tail(run.get("stdout", "")),
        "stderr_tail": _tail(run.get("stderr", "")),
        "duration_ms": run.get("duration_ms"),
    }
    payload.update(extra)
    return payload


def resolve_path_arg(value):
    """Resolve a client-supplied path: absolute stays, relative is repo-relative.

    ``~`` and ``$VAR`` / ``%VAR%`` are expanded so a client can pass
    ``$HERMES_HOME/memories/MEMORY.md``.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = os.path.expandvars(os.path.expanduser(text))
    path = Path(text)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        return path.resolve()
    except OSError:
        return path


def guard_default_memory_file():
    explicit = os.environ.get("MEM_GUARD_MEMORY")
    if explicit:
        return str(resolve_path_arg(explicit))
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return str(Path(home) / "memories" / "MEMORY.md")


# ─────────────────────────────── parsers ────────────────────────────────
#
# The four engines print human-readable reports. We never modify them, so each
# parser is tolerant: unknown lines are ignored, multi-line entries stay intact,
# and the unparsed text is always returned as ``raw_report`` so a caller can
# still read what the engine said.


GUARD_HEAD_RE = re.compile(r"需人工决策\s*(\d+)\s*条")
GUARD_FIX_RE = re.compile(r"自动修复\s*(\d+)\s*条")
GUARD_OK_RE = re.compile(r"正常\s*(\d+).*?待全量版\s*(\d+).*?指针总数\s*(\d+)")
GUARD_ITEM_RE = re.compile(r"^\s+(?:L(\d+)\s+)?(\S+?):\s*(.*)$")
GUARD_MISSING_RE = re.compile(r"指针文件不存在[:：]\s*(.+)")


def parse_guard_report(text):
    """Parse mem_guard's 🔴/🟡/✅ report into counts + item lists."""
    parsed = {
        "red": [],
        "yellow": [],
        "counts": {"red": 0, "yellow": 0, "normal": 0, "pending": 0, "pointers_total": 0},
        "reported": {"red": None, "yellow": None},
    }
    section = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("🔴"):
            section = "red"
            match = GUARD_HEAD_RE.search(stripped)
            if match:
                parsed["reported"]["red"] = int(match.group(1))
            continue
        if stripped.startswith("🟡"):
            section = "yellow"
            match = GUARD_FIX_RE.search(stripped)
            if match:
                parsed["reported"]["yellow"] = int(match.group(1))
            continue
        if stripped.startswith("✅"):
            match = GUARD_OK_RE.search(stripped)
            if match:
                parsed["counts"]["normal"] = int(match.group(1))
                parsed["counts"]["pending"] = int(match.group(2))
                parsed["counts"]["pointers_total"] = int(match.group(3))
            section = None
            continue
        if stripped.startswith("🛡"):
            continue
        if section and stripped[0] in "·…":
            # Continuation of the previous entry (watermark item previews etc.).
            if parsed[section]:
                parsed[section][-1]["message"] += "\n" + stripped
            continue
        if section:
            match = GUARD_ITEM_RE.match(line)
            if match:
                parsed[section].append({
                    "line": int(match.group(1)) if match.group(1) else None,
                    "key": match.group(2).strip(),
                    "message": match.group(3).strip(),
                })
    # Counts come from the items actually parsed; the header figures are kept
    # separately so a format drift is visible instead of silent.
    parsed["counts"]["red"] = len(parsed["red"])
    parsed["counts"]["yellow"] = len(parsed["yellow"])
    return parsed


LINK_HEAD_RE = re.compile(
    r"^===\s+\[(?P<dir>.+?)\]\s+"
    r"(?P<kind>路径悬空引用|技能名引用失效|fact#\s*引用清单)"
    r"(?:[:：]\s*(?P<n1>\d+)\s*处|（\s*(?P<n2>\d+)\s*处[^）]*）)\s*===$"
)
LINK_ITEM_RE = re.compile(r"^\s{2}(?P<src>.+?)\s+→\s+(?P<dst>.+?)\s*$")
LINK_HIDDEN_RE = re.compile(r"^\s*\.\.\.\s*其余\s*(\d+)\s*条")
LINK_TOTAL_RE = re.compile(r"共\s*(\d+)\s*处\s*error\s*级失效引用")
LINK_SKILL_INDEX_RE = re.compile(r"技能库索引[:：]\s*(\d+)\s*个技能名")
LINK_SKIP_RE = re.compile(r"^跳过（不存在）[:：]\s*(.+)$")
LINK_DEAD_SKILL_RE = re.compile(r"^技能「(.+?)」不存在$")
LINK_FACT_IDS_RE = re.compile(r"涉及\s*fact\s*\[([^\]]*)\]")


def parse_scan_links(text):
    """Parse scan_links' per-directory sections into structured findings."""
    result = {
        "skill_index_size": None,
        "dirs": [],
        "skipped_dirs": [],
        "errors_total": None,
        "clean": None,
        "truncated": False,
        "hidden_items": 0,
    }
    current = None
    kind = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        match = LINK_SKILL_INDEX_RE.search(stripped)
        if match:
            result["skill_index_size"] = int(match.group(1))
            continue
        match = LINK_SKIP_RE.match(stripped)
        if match:
            result["skipped_dirs"].append(match.group(1).strip())
            continue
        match = LINK_HEAD_RE.match(stripped)
        if match:
            name = match.group("dir")
            current = {"dir": name, "dangling_paths": [], "dead_skill_refs": [],
                       "fact_refs": None, "counts": {}, "hidden": {}}
            result["dirs"].append(current)
            raw_kind = match.group("kind")
            kind = ("fact" if raw_kind.startswith("fact")
                    else "skill" if "技能" in raw_kind else "path")
            shown_total = match.group("n1") or match.group("n2") or "0"
            current["counts"][kind] = int(shown_total)
            if kind == "fact":
                ids = LINK_FACT_IDS_RE.search(stripped)
                raw_ids = ids.group(1) if ids else ""
                current["fact_refs"] = {
                    "count": int(shown_total),
                    "ids": [t.strip() for t in raw_ids.split(",") if t.strip()],
                }
            continue
        match = LINK_HIDDEN_RE.match(line)
        if match and current is not None and kind:
            hidden = int(match.group(1))
            current["hidden"][kind] = current["hidden"].get(kind, 0) + hidden
            result["truncated"] = True
            result["hidden_items"] += hidden
            continue
        match = LINK_TOTAL_RE.search(stripped)
        if match:
            result["errors_total"] = int(match.group(1))
            continue
        if "✅" in stripped and "无失效引用" in stripped:
            result["errors_total"] = 0
            continue
        if current is None or kind in (None, "fact"):
            continue
        match = LINK_ITEM_RE.match(line)
        if not match:
            continue
        src, dst = match.group("src"), match.group("dst")
        if kind == "skill":
            dead = LINK_DEAD_SKILL_RE.match(dst)
            current["dead_skill_refs"].append({
                "source": src,
                "skill": dead.group(1) if dead else dst,
            })
        else:
            current["dangling_paths"].append({"source": src, "ref": dst})

    if result["errors_total"] is None:
        # Older/newer wording: fall back to the per-section counts.
        total = 0
        for entry in result["dirs"]:
            total += entry["counts"].get("path", 0) + entry["counts"].get("skill", 0)
        result["errors_total"] = total
    result["clean"] = result["errors_total"] == 0
    return result


L2_LINE_RE = re.compile(
    r"^(?P<key>\S+)\s+\[(?P<scheme>[a-z]+)\]\s+summary=(?P<summary>\d+)\s+"
    r"L2chars=(?P<l2chars>\d+)\s+missing_at_L2=(?P<missing>\d+)\s*->\s*(?P<tokens>.*)$"
)


def _parse_token_list(fragment):
    fragment = (fragment or "").strip()
    if not fragment:
        return []
    try:
        value = ast.literal_eval(fragment)
    except (ValueError, SyntaxError):
        return [fragment]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def parse_l2_coverage(text):
    entries = []
    for raw_line in text.splitlines():
        match = L2_LINE_RE.match(raw_line.strip())
        if not match:
            continue
        missing = int(match.group("missing"))
        entries.append({
            "key": match.group("key"),
            "scheme": match.group("scheme"),
            "summary_chars": int(match.group("summary")),
            "l2_chars": int(match.group("l2chars")),
            "missing_at_L2_count": missing,
            "missing_at_L2": _parse_token_list(match.group("tokens")),
        })
    return entries


# ──────────────────────────────── tools ─────────────────────────────────


TIMEOUT_SCHEMA = {
    "type": "number",
    "description": "Per-call engine timeout in seconds (default 120, max 600).",
}
MEMORY_FILE_SCHEMA = {
    "type": "string",
    "description": (
        "Path to the pointer/memory markdown file (absolute, or relative to the "
        "pulse-path repo root; ~ / $HERMES_HOME are expanded). Omit to use the "
        "engine default: $MEM_GUARD_MEMORY, else $HERMES_HOME/memories/MEMORY.md."
    ),
}


def tool_guard_check(args):
    mem_value = args.get("memory_file")
    resolved = resolve_path_arg(mem_value) if mem_value not in (None, "") else None
    if resolved is not None and not resolved.is_file():
        return {
            "ok": False,
            "status": "path_not_found",
            "engine": ENGINE_LABELS["guard"],
            "error": {"kind": "path_not_found",
                      "message": "memory_file not found: %s" % resolved},
            "memory_file": str(resolved),
        }

    env_extra = {"MEM_GUARD_MEMORY": str(resolved) if resolved is not None else None}
    run = run_engine("guard", ["--check", "--verbose"], env_extra=env_extra,
                     timeout_s=args.get("timeout_s"))
    if not run["ok"]:
        return engine_failure_payload("guard", run)

    text = run["stdout"]
    missing = GUARD_MISSING_RE.search(text)
    if missing:
        payload = {
            "ok": False,
            "status": "memory_file_missing",
            "engine": ENGINE_LABELS["guard"],
            "error": {"kind": "memory_file_missing",
                      "message": "the engine has no pointer file to check: %s" % missing.group(1).strip()},
            "hint": "pass memory_file, or export MEM_GUARD_MEMORY / HERMES_HOME",
            "duration_ms": run["duration_ms"],
            "raw_report": text.strip(),
        }
        return payload

    parsed = parse_guard_report(text)
    counts = parsed["counts"]
    payload = {
        "ok": True,
        "status": "checked",
        "engine": ENGINE_LABELS["guard"],
        "mode": "check-only (--check: never rewrites the pointer file)",
        "memory_file": str(resolved) if resolved is not None else guard_default_memory_file(),
        "memory_file_source": "argument" if resolved is not None else "engine default",
        "counts": counts,
        "red": parsed["red"],
        "yellow": parsed["yellow"],
        "clean": counts["red"] == 0 and counts["yellow"] == 0,
        "verdict": ("clean" if counts["red"] == 0 and counts["yellow"] == 0
                    else "needs_attention"),
        "engine_exit_code": run["returncode"],
        "duration_ms": run["duration_ms"],
        "raw_report": text.strip(),
    }
    if parsed["reported"]["red"] is not None and parsed["reported"]["red"] != counts["red"]:
        payload["warning"] = ("parsed %d red item(s) but the report header announced %d — "
                              "the engine's report format may have changed"
                              % (counts["red"], parsed["reported"]["red"]))
    return payload


def tool_scan_links(args):
    dirs_value = args.get("dirs")
    if dirs_value is None:
        dirs_value = [str(REPO_ROOT)]
    if not isinstance(dirs_value, list) or not dirs_value:
        raise ValueError("dirs must be a non-empty array of directory paths")
    for item in dirs_value:
        if not isinstance(item, str):
            raise ValueError("dirs must contain strings only")

    resolved = [resolve_path_arg(item) for item in dirs_value]
    if any(path is None for path in resolved):
        raise ValueError("dirs must contain non-empty paths")
    for path in resolved:
        if not path.is_dir():
            return {
                "ok": False,
                "status": "path_not_found",
                "engine": ENGINE_LABELS["scan_links"],
                "error": {"kind": "path_not_found",
                          "message": "directory not found: %s" % path},
            }

    run = run_engine("scan_links", [str(p) for p in resolved], timeout_s=args.get("timeout_s"))
    if not run["ok"]:
        return engine_failure_payload("scan_links", run)

    parsed = parse_scan_links(run["stdout"])
    # The engine only prints a section when a directory has findings, so build one
    # entry per requested directory (in request order) and merge the parsed rows in.
    buckets = {}
    for entry in parsed["dirs"]:
        buckets.setdefault(entry["dir"], []).append(entry)
    dir_reports = []
    for path in resolved:
        name = path.name or str(path)
        bucket = buckets.get(name) or []
        entry = bucket.pop(0) if bucket else {
            "dir": name, "dangling_paths": [], "dead_skill_refs": [],
            "fact_refs": None, "counts": {}, "hidden": {},
        }
        entry["path"] = str(path)
        dir_reports.append(entry)
    for leftovers in buckets.values():
        dir_reports.extend(leftovers)

    payload = {
        "ok": True,
        "status": "checked",
        "engine": ENGINE_LABELS["scan_links"],
        "dirs_requested": [str(p) for p in resolved],
        "skill_index_size": parsed["skill_index_size"],
        "errors_total": parsed["errors_total"],
        "clean": parsed["clean"],
        "dirs": dir_reports,
        "skipped_dirs": parsed["skipped_dirs"],
        "findings_truncated": parsed["truncated"],
        "hidden_findings": parsed["hidden_items"],
        "engine_exit_code": run["returncode"],
        "duration_ms": run["duration_ms"],
        "raw_report": run["stdout"].strip(),
    }
    if parsed["truncated"]:
        payload["note"] = ("scan_links prints at most 15 findings per category per directory; "
                           "counts above are the engine's exact totals, the listed items are a preview")
    return payload


def tool_l2_coverage(args):
    mem_value = args.get("memory_file")
    resolved = resolve_path_arg(mem_value) if mem_value not in (None, "") else None
    if resolved is not None and not resolved.is_file():
        return {
            "ok": False,
            "status": "path_not_found",
            "engine": ENGINE_LABELS["l2_coverage"],
            "error": {"kind": "path_not_found",
                      "message": "memory_file not found: %s" % resolved},
            "memory_file": str(resolved),
        }

    argv = [str(resolved)] if resolved is not None else []
    run = run_engine("l2_coverage", argv, timeout_s=args.get("timeout_s"))
    if not run["ok"]:
        return engine_failure_payload("l2_coverage", run)

    text = run["stdout"]
    if "memory file not found:" in text:
        return {
            "ok": False,
            "status": "memory_file_missing",
            "engine": ENGINE_LABELS["l2_coverage"],
            "error": {"kind": "memory_file_missing",
                      "message": text.strip().splitlines()[0] if text.strip() else "memory file not found"},
            "hint": "pass memory_file, or export HERMES_HOME",
            "duration_ms": run["duration_ms"],
            "raw_report": text.strip(),
        }
    if "cannot read" in text:
        return {
            "ok": False,
            "status": "engine_error",
            "engine": ENGINE_LABELS["l2_coverage"],
            "error": {"kind": "unreadable_memory_file", "message": text.strip()},
            "duration_ms": run["duration_ms"],
        }

    entries = parse_l2_coverage(text)
    missing = [{"key": e["key"], "scheme": e["scheme"], "missing_at_L2": e["missing_at_L2"]}
               for e in entries if e["missing_at_L2"]]
    payload = {
        "ok": True,
        "status": "checked",
        "engine": ENGINE_LABELS["l2_coverage"],
        "memory_file": str(resolved) if resolved is not None else None,
        "memory_file_source": "argument" if resolved is not None else "engine default",
        "pointers_checked": len(entries),
        "pointers_with_missing": len(missing),
        "missing_at_L2": missing,
        "entries": entries,
        "clean": not missing,
        "note": ("the engine only inspects pointers whose summary exceeds 40 characters; "
                 "a token absent at L2 may be stale rather than missing — verify before trimming"),
        "engine_exit_code": run["returncode"],
        "duration_ms": run["duration_ms"],
        "raw_report": text.strip(),
    }
    if not entries and text.strip():
        payload["note"] += "; no pointer rows were reported for this file"
    return payload


def tool_secrets_scan(args):
    raw_path = args.get("path")
    target = resolve_path_arg(raw_path) if raw_path not in (None, "") else REPO_ROOT
    if not target.exists():
        return {
            "ok": False,
            "status": "path_not_found",
            "engine": ENGINE_LABELS["secrets"],
            "error": {"kind": "path_not_found", "message": "path not found: %s" % target},
        }

    run = run_engine("secrets", [str(target), "--json"], timeout_s=args.get("timeout_s"))
    if not run["ok"]:
        return engine_failure_payload("secrets", run)

    try:
        data = json.loads(run["stdout"])
    except ValueError as exc:
        return bad_output_payload(
            "secrets",
            "check_secrets.py --json did not emit JSON (%s)" % exc,
            run,
            path=str(target),
        )
    if not isinstance(data, dict):
        return bad_output_payload("secrets", "check_secrets.py --json emitted non-object JSON", run,
                                  path=str(target))

    findings = []
    files = data.get("files") or []
    for item in files:
        if not isinstance(item, dict):
            continue
        for row in item.get("findings") or []:
            try:
                lineno, rule, sample = row[0], row[1], row[2]
            except (TypeError, IndexError):
                continue
            findings.append({"file": item.get("file"), "line": lineno,
                             "rule": rule, "sample": sample})
    total = data.get("total_findings")
    if not isinstance(total, int):
        total = len(findings)
    limit = 200
    payload = {
        "ok": True,
        "status": "checked",
        "engine": ENGINE_LABELS["secrets"],
        "path": str(target),
        "total_findings": total,
        "clean": total == 0,
        "result": "CLEAN" if total == 0 else "FINDINGS PRESENT",
        "files_scanned": len(files),
        "findings": findings[:limit],
        "findings_truncated": len(findings) > limit,
        "note": "samples are masked by the engine (first characters only) and are safe to share",
        "engine_exit_code": run["returncode"],
        "duration_ms": run["duration_ms"],
    }
    return payload


# ───────────────────────── L2 search (pp_index.py) ──────────────────────
#
# pp_index.py owns all search semantics (SQLite FTS5 trigram, LIKE fallback for
# queries under 3 characters, incremental build). This layer only validates
# arguments, maps exit code 2 to a typed error and reshapes the payload, so the
# engine stays the single source of truth for hit content.

INDEX_DEFAULT_LIMIT = 8
INDEX_MAX_LIMIT = 200


def _json_object(text):
    """Best-effort JSON object out of an engine's stdout (tolerates noise around it)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def tool_pp_search(args):
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    query = query.strip()

    limit = args.get("limit")
    if limit is None:
        limit = INDEX_DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int) \
            or not 1 <= limit <= INDEX_MAX_LIMIT:
        raise ValueError("limit must be an integer between 1 and %d" % INDEX_MAX_LIMIT)

    rebuild = args.get("rebuild")
    if rebuild is None:
        rebuild = False
    if not isinstance(rebuild, bool):
        raise ValueError("rebuild must be a boolean")

    build_run = None
    if rebuild:
        build_run = run_engine("index", ["--build", "--json"], timeout_s=args.get("timeout_s"))
        if not build_run["ok"]:
            return engine_failure_payload("index", build_run)
        if build_run["returncode"] != 0:
            built = _json_object(build_run["stdout"]) or {}
            err = built.get("error") or {}
            return {
                "ok": False,
                "status": "engine_error",
                "engine": ENGINE_LABELS["index"],
                "query": query,
                "error": {"kind": err.get("kind") or "build_failed",
                          "message": err.get("message") or "pp_index.py --build failed",
                          "exit_code": build_run["returncode"]},
                "hint": err.get("hint") or "check $HERMES_HOME, the pointer file and sqlite FTS5 support",
                "stdout_tail": _tail(build_run["stdout"]),
                "stderr_tail": _tail(build_run["stderr"]),
                "duration_ms": build_run["duration_ms"],
            }

    run = run_engine("index", ["--search", query, "--limit", str(limit), "--json"],
                     timeout_s=args.get("timeout_s"))
    if not run["ok"]:
        return engine_failure_payload("index", run)

    data = _json_object(run["stdout"])
    if run["returncode"] == 2:
        err = (data or {}).get("error") or {}
        payload = {
            "ok": False,
            "status": "index_missing" if err.get("kind") == "index_missing" else "engine_error",
            "engine": ENGINE_LABELS["index"],
            "query": query,
            "error": {"kind": err.get("kind") or "engine_error",
                      "message": err.get("message") or "pp_index.py --search failed",
                      "exit_code": 2},
            "hint": err.get("hint") or "call pp_search again with rebuild=true, or run pp_index.py --build",
            "stderr_tail": _tail(run["stderr"]),
            "duration_ms": run["duration_ms"],
        }
        if data is not None:
            payload["raw_report"] = run["stdout"].strip()
        return payload
    if data is None:
        return bad_output_payload("index",
                                 "pp_index.py --search --json did not emit JSON",
                                 run, query=query)

    hits = []
    for item in data.get("hits") or []:
        if not isinstance(item, dict):
            continue
        hit = {"path": item.get("path"), "score": item.get("score"),
               "snippet": item.get("snippet")}
        if item.get("pointer_key"):
            hit["pointer_key"] = item["pointer_key"]
        if item.get("pointer_keys"):
            hit["pointer_keys"] = item["pointer_keys"]
        if item.get("source"):
            hit["source"] = item["source"]
        hits.append(hit)

    payload = {
        "ok": True,
        "status": "searched",
        "engine": ENGINE_LABELS["index"],
        "query": data.get("query") or query,
        "mode": data.get("mode"),
        "hits": hits,
        "hits_total": data.get("hits_total", len(hits)),
        "index": data.get("index") or {},
        "engine_exit_code": run["returncode"],
        "duration_ms": run["duration_ms"],
        "raw_report": run["stdout"].strip(),
    }
    if build_run is not None:
        built = _json_object(build_run["stdout"]) or {}
        payload["rebuilt"] = True
        payload["build"] = {
            "docs": (built.get("index") or {}).get("docs"),
            "added": (built.get("build") or {}).get("added"),
            "updated": (built.get("build") or {}).get("updated"),
            "skipped": (built.get("build") or {}).get("skipped"),
            "removed": (built.get("build") or {}).get("removed"),
        }
    if not hits:
        payload["note"] = data.get("note") or "no document in the index matched this query"
    return payload


TOOLS = [
    {
        "name": "pp_guard_check",
        "title": "pulse-path pointer guard check (read-only)",
        "description": (
            "Run the mem_guard pointer-dictionary health check in check-only mode "
            "(--check, never rewrites the memory file). Verifies every "
            "key = scheme://target | summary pointer, detects mangled lines, duplicate "
            "keys, over-long summaries and memory watermark pressure. Returns "
            "red/yellow/normal counts plus per-item detail (line, key, message)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_file": MEMORY_FILE_SCHEMA,
                "timeout_s": TIMEOUT_SCHEMA,
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "handler": tool_guard_check,
    },
    {
        "name": "pp_scan_links",
        "title": "pulse-path knowledge-link lint (read-only)",
        "description": (
            "Scan directories for broken knowledge links: dangling absolute file "
            "paths, references to skills that do not exist, and fact# references "
            "(listed for an upper layer to verify). Engine exit code 1 with findings "
            "is a normal result, not a failure. Returns per-directory findings plus "
            "exact error totals."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dirs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Directories to scan (absolute, or relative to the pulse-path "
                        "repo root). Defaults to the repo root."
                    ),
                },
                "timeout_s": TIMEOUT_SCHEMA,
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "handler": tool_scan_links,
    },
    {
        "name": "pp_l2_coverage",
        "title": "pulse-path L2 coverage pre-flight (read-only)",
        "description": (
            "Before shrinking a pointer summary, check that every distinctive token "
            "(cron id, *.py name, absolute path, number) already exists at the "
            "pointer's L2 target. Returns, per pointer, the tokens missing at L2 so "
            "only provably covered tokens get dropped."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_file": MEMORY_FILE_SCHEMA,
                "timeout_s": TIMEOUT_SCHEMA,
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "handler": tool_l2_coverage,
    },
    {
        "name": "pp_secrets_scan",
        "title": "pulse-path pre-publish secret scan (read-only)",
        "description": (
            "Scan a file or directory tree for credentials, tokens, private keys, "
            "high-entropy strings, e-mail addresses, private IPs, MAC addresses and "
            "absolute user paths — the pre-publish gate. Matches are masked by the "
            "engine. Returns the finding count and whether the result is CLEAN."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File or directory to scan (absolute, or relative to the "
                        "pulse-path repo root). Defaults to the repo root."
                    ),
                },
                "timeout_s": TIMEOUT_SCHEMA,
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "handler": tool_secrets_scan,
    },
    {
        "name": "pp_search",
        "title": "pulse-path L2 full-text search (offline)",
        "description": (
            "Search the L2 bodies behind the pointers — doc:// files and skill:// "
            "SKILL.md plus references/*.md — through an offline SQLite FTS5 index "
            "(trigram tokenizer, so Chinese and English substrings both match). A "
            "query shorter than 3 characters falls back to a LIKE scan and the payload "
            "says mode=\"like\". Returns hits (path / score / snippet / pointer_key) and "
            "the index snapshot (docs / built_at / stale). rebuild=true refreshes the "
            "disposable index cache under $HERMES_HOME/.pp_index first (incremental: "
            "unchanged files are skipped); the repo and the memory store are never written."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords to look for. Space-separated terms are ANDed.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 8,
                    "description": "Maximum number of hits to return (default 8, max 200).",
                },
                "rebuild": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Build/refresh the index before searching. Incremental — files whose "
                        "mtime and size are unchanged are skipped."
                    ),
                },
                "timeout_s": TIMEOUT_SCHEMA,
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
        "handler": tool_pp_search,
    },
]

TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}

INSTRUCTIONS = (
    "pulse-path exposes read-only memory-pointer health checks. "
    "pp_guard_check = pointer dictionary validation (red/yellow/normal), "
    "pp_scan_links = broken knowledge-link lint, "
    "pp_l2_coverage = pre-flight check before trimming a pointer summary, "
    "pp_secrets_scan = pre-publish leak scan, "
    "pp_search = offline full-text search over the L2 bodies behind the pointers "
    "(SQLite FTS5; rebuild=true refreshes its own disposable cache). "
    "All tools are idempotent, never modify the memory store, and never touch the "
    "repository; path arguments may be absolute or relative to the pulse-path repo root."
)


# ─────────────────────────── JSON-RPC dispatch ──────────────────────────


def _rpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


def _public_tools():
    return [
        {
            "name": tool["name"],
            "title": tool["title"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
            "annotations": tool["annotations"],
        }
        for tool in TOOLS
    ]


def handle_request(message):
    """Turn one decoded request into a response dict (None = no response)."""
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if not isinstance(method, str):
        return _rpc_error(msg_id, -32600, "Invalid Request: method must be a string")

    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        version = (requested if requested in SUPPORTED_PROTOCOL_VERSIONS
                   else DEFAULT_PROTOCOL_VERSION)
        return _rpc_result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        })

    if method == "ping":
        return _rpc_result(msg_id, {})

    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": _public_tools()})

    if method == "tools/call":
        if not isinstance(params, dict):
            return _rpc_error(msg_id, -32602, "Invalid params: expected an object")
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOLS_BY_NAME:
            return _rpc_error(msg_id, -32602,
                              "Invalid params: unknown tool %r" % (name,),
                              {"available": sorted(TOOLS_BY_NAME)})
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _rpc_error(msg_id, -32602, "Invalid params: arguments must be an object")
        unknown = sorted(set(arguments) - set(TOOLS_BY_NAME[name]["inputSchema"]["properties"]))
        if unknown:
            return _rpc_error(msg_id, -32602,
                              "Invalid params: unexpected argument(s) %s for %s"
                              % (", ".join(unknown), name),
                              {"accepted": sorted(TOOLS_BY_NAME[name]["inputSchema"]["properties"])})
        try:
            payload = TOOLS_BY_NAME[name]["handler"](arguments)
        except ValueError as exc:
            return _rpc_error(msg_id, -32602, "Invalid params: %s" % exc)
        except Exception as exc:                     # noqa: BLE001 - report, never crash
            log("tool %s raised: %s" % (name, traceback.format_exc()))
            payload = {"ok": False, "status": "internal_error",
                       "error": {"kind": "exception", "message": str(exc)}}
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        return _rpc_result(msg_id, {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": not payload.get("ok", False),
        })

    if method in ("notifications/initialized", "notifications/cancelled",
                  "notifications/progress", "notifications/roots/list_changed"):
        return None

    return _rpc_error(msg_id, -32601, "Method not found: %s" % method)


def serve_stream(stdin, stdout):
    """Frame-by-frame serve loop: read one message, answer, repeat until EOF."""
    messages = iter_messages(stdin)
    while True:
        try:
            frame = next(messages, None)
        except ValueError as exc:
            write_message(stdout, _rpc_error(None, -32700, "Parse error: %s" % exc))
            continue
        if frame is None:
            return
        if not isinstance(frame, dict):
            write_message(stdout, _rpc_error(None, -32600,
                                             "Invalid Request: expected a single JSON object"))
            continue
        is_notification = "id" not in frame
        if "method" not in frame:
            continue
        try:
            response = handle_request(frame)
        except Exception:                            # noqa: BLE001
            log("unhandled error: %s" % traceback.format_exc())
            if is_notification:
                continue
            response = _rpc_error(frame.get("id"), -32603, "Internal error")
        if response is not None and not is_notification:
            write_message(stdout, response)


HELP = """pulse-path MCP server %s — stdio JSON-RPC 2.0, zero dependencies

Tools (read-only, idempotent):
  pp_guard_check    memory_file?  mem_guard.py --check --verbose
  pp_scan_links     dirs[]?       scan_links.py
  pp_l2_coverage    memory_file?  check_l2_coverage.py
  pp_secrets_scan   path?         check_secrets.py --json
  pp_search         query, limit?, rebuild?  pp_index.py --search (FTS5)

Framing: newline-delimited JSON (one message per line, UTF-8).
         'Content-Length: N' header frames are accepted as a fallback.
Repo root: %s

Flags:
  --help, -h      this text (nothing is served)
  --print-tools   dump the tool schemas as JSON and exit
  --version       print the server version and exit

Environment:
  PULSE_PATH_MCP_TIMEOUT   per-engine timeout, seconds (default 120, max 600)
  HERMES_HOME, MEM_GUARD_* are passed through to the engines unchanged.

Register it (Claude Desktop / Claude Code / any MCP client) with
  command: python  args: ["<repo>/mcp_server/pulse_path_mcp.py"]
See mcp_server/README.md for copy-paste configs and limits.

Self-test (no MCP client needed):
  python mcp_server/test_mcp.py
""" % (SERVER_VERSION, REPO_ROOT)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(HELP)
        return 0
    if "--version" in argv:
        sys.stdout.write("%s %s\n" % (SERVER_NAME, SERVER_VERSION))
        return 0
    if "--print-tools" in argv:
        sys.stdout.write(json.dumps({"tools": _public_tools()}, ensure_ascii=False, indent=1) + "\n")
        return 0

    for key, path in ENGINE_PATHS.items():
        if not path.is_file():
            log("warning: engine %s is missing (%s)" % (key, path))

    log("serving %s %s from %s" % (SERVER_NAME, SERVER_VERSION, REPO_ROOT))
    serve_stream(sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
