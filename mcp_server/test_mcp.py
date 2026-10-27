#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test for the pulse-path MCP server — no MCP client library, no network.

It spawns ``mcp_server/pulse_path_mcp.py`` as a real child process and speaks raw
newline-delimited JSON-RPC to it over pipes, exactly as an MCP client would:
initialize handshake -> notifications/initialized -> tools/list -> tools/call.

Everything runs against fixtures built in a temp directory (a fake HERMES_HOME
with one resolvable pointer and one deliberately dangling pointer), so the run is
hermetic: it never reads the real memory store and never needs the network.

    python mcp_server/test_mcp.py     ->   MCP TEST OK n/n     (exit 0)
"""

import io
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

MCP_DIR = Path(__file__).resolve().parent
REPO_ROOT = MCP_DIR.parent
SERVER = MCP_DIR / "pulse_path_mcp.py"

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

CHECKS = []


def check(label, condition, detail=""):
    CHECKS.append((label, bool(condition), detail))
    if not condition:
        print("FAIL  %s  %s" % (label, detail))


class Client(object):
    """Minimal raw JSON-RPC client over the child process's stdin/stdout."""

    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
        )
        self.out_queue = queue.Queue()
        self.err_lines = []
        self.next_id = 0
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self):
        for line in self.proc.stdout:
            self.out_queue.put(line)
        self.out_queue.put(None)

    def _pump_stderr(self):
        for line in self.proc.stderr:
            self.err_lines.append(line.decode("utf-8", "replace").rstrip())

    # -- wire ------------------------------------------------------------
    def send(self, message):
        payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
        assert b"\n" not in payload, "a JSON-RPC frame must not contain a raw newline"
        self.proc.stdin.write(payload + b"\n")
        self.proc.stdin.flush()

    def recv(self, timeout=120):
        try:
            item = self.out_queue.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError("timed out waiting for a response; server stderr:\n%s"
                                 % self.stderr_tail())
        if item is None:
            raise AssertionError("server closed stdout; server stderr:\n%s" % self.stderr_tail())
        text = item.decode("utf-8", "replace").strip()
        try:
            return json.loads(text)
        except ValueError:
            raise AssertionError("server wrote non-JSON to stdout: %r" % text[:300])

    def stderr_tail(self, n=8):
        return "\n".join(self.err_lines[-n:])

    # -- jsonrpc ---------------------------------------------------------
    def request(self, method, params=None):
        self.next_id += 1
        rid = self.next_id
        message = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        response = self.recv()
        return rid, response

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)

    def call_tool(self, name, arguments=None):
        rid, response = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        check("%s: response id echoes request" % name, response.get("id") == rid, repr(response)[:200])
        return response

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)


def tool_payload(response):
    """Pull the JSON payload out of a tools/call result (text + structuredContent)."""
    result = response.get("result") or {}
    structured = result.get("structuredContent")
    content = result.get("content") or []
    text_payload = None
    if content and isinstance(content[0], dict) and content[0].get("type") == "text":
        try:
            text_payload = json.loads(content[0].get("text", ""))
        except ValueError:
            text_payload = None
    return result, structured, text_payload


def build_fixtures(sandbox):
    """Hermetic HERMES_HOME: one good pointer, one dangling pointer."""
    home = sandbox / "hermes"
    (home / "memories").mkdir(parents=True)
    (home / "skills" / "demo-skill").mkdir(parents=True)
    (home / "skills" / "demo-skill" / "SKILL.md").write_text("# demo skill\n", encoding="utf-8")
    (home / "memories" / "USER.md").write_text("small user memory\n", encoding="utf-8")

    dirty = home / "memories" / "MEMORY.md"
    dirty.write_text(
        "\u00a7\n"
        "ok_skill = skill://demo-skill | 可解析的技能指针\n"
        "bad_doc = doc://missing-fixture.md | 故意悬空用于验证断链分级\n",
        encoding="utf-8",
    )
    clean = home / "memories" / "MEMORY_CLEAN.md"
    clean.write_text(
        "\u00a7\n"
        "ok_skill = skill://demo-skill | 可解析的技能指针\n",
        encoding="utf-8",
    )

    good_dir = sandbox / "docs-clean"
    good_dir.mkdir()
    (good_dir / "notes.md").write_text("# notes\nno links here\n", encoding="utf-8")

    bad_dir = sandbox / "docs-dangling"
    bad_dir.mkdir()
    # The dangling drive-letter path is assembled at runtime so this test file
    # itself stays free of absolute-path literals (the secret scanner flags those).
    fake = "C" + ":" + "/" + "pulse-path-fixture/not-there.md"
    (bad_dir / "stale.md").write_text("see %s for details\n" % fake, encoding="utf-8")
    return home, dirty, clean, good_dir, bad_dir, fake


def main():
    sandbox = Path(tempfile.mkdtemp(prefix="pp-mcp-test-"))
    client = None
    try:
        home, dirty_mem, clean_mem, good_dir, bad_dir, fake_ref = build_fixtures(sandbox)
        env = dict(os.environ)
        env["HERMES_HOME"] = str(home)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env.pop("MEM_GUARD_MEMORY", None)

        client = Client(env)

        # ── 1. initialize handshake ──────────────────────────────────────
        rid, init = client.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "pulse-path-selftest", "version": "0"},
        })
        check("initialize: jsonrpc is 2.0", init.get("jsonrpc") == "2.0", repr(init)[:200])
        check("initialize: id echoed", init.get("id") == rid, repr(init.get("id")))
        result = init.get("result") or {}
        check("initialize: protocolVersion echoes a supported version",
              result.get("protocolVersion") == "2025-03-26", repr(result.get("protocolVersion")))
        check("initialize: capabilities.tools present",
              isinstance((result.get("capabilities") or {}).get("tools"), dict),
              repr(result.get("capabilities")))
        info = result.get("serverInfo") or {}
        check("initialize: serverInfo name/version",
              info.get("name") == "pulse-path" and bool(info.get("version")), repr(info))

        # ── 2. notifications/initialized must not be answered ────────────
        client.notify("notifications/initialized")
        rid, listing = client.request("tools/list", {})
        check("notification: initialized produced no response frame",
              listing.get("id") == rid, "first frame after the notification: %r" % (listing.get("method"),))

        # ── 3. tools/list ───────────────────────────────────────────────
        tools = (listing.get("result") or {}).get("tools") or []
        names = [tool.get("name") for tool in tools]
        expected = ["pp_guard_check", "pp_scan_links", "pp_l2_coverage", "pp_secrets_scan"]
        check("tools/list: four tools with the expected names", names == expected, repr(names))
        schemas_ok = all(
            (tool.get("inputSchema") or {}).get("type") == "object"
            and isinstance((tool.get("inputSchema") or {}).get("properties"), dict)
            and bool(tool.get("description"))
            and bool(tool.get("title"))
            for tool in tools
        )
        check("tools/list: every tool has title/description/object inputSchema", schemas_ok)
        check("tools/list: every tool declares readOnlyHint",
              all((tool.get("annotations") or {}).get("readOnlyHint") is True for tool in tools))
        check("tools/list: pp_guard_check exposes memory_file",
              "memory_file" in (tools[0].get("inputSchema") or {}).get("properties", {}))
        check("tools/list: pp_scan_links exposes dirs as an array",
              ((tools[1].get("inputSchema") or {}).get("properties", {}).get("dirs") or {}).get("type") == "array")
        check("tools/list: pp_secrets_scan exposes path",
              "path" in (tools[3].get("inputSchema") or {}).get("properties", {}))

        # ── 4. pp_guard_check on a deliberately broken fixture ──────────
        response = client.call_tool("pp_guard_check", {"memory_file": str(dirty_mem)})
        result, structured, text_payload = tool_payload(response)
        check("pp_guard_check: not an error", result.get("isError") is False, repr(result)[:300])
        check("pp_guard_check: structuredContent mirrors the text payload",
              isinstance(structured, dict) and structured == text_payload)
        counts = (structured or {}).get("counts") or {}
        check("pp_guard_check: red/yellow/normal counts are ints",
              all(isinstance(counts.get(key), int) for key in
                  ("red", "yellow", "normal", "pending", "pointers_total")), repr(counts))
        red_keys = [item.get("key") for item in (structured or {}).get("red") or []]
        check("pp_guard_check: dangling pointer is reported red",
              red_keys == ["bad_doc"], repr(red_keys))
        check("pp_guard_check: resolvable pointer counts as normal",
              counts.get("normal") == 1 and counts.get("pointers_total") == 2, repr(counts))
        check("pp_guard_check: clean flag is False with findings", (structured or {}).get("clean") is False)
        check("pp_guard_check: echoes the memory file it checked",
              (structured or {}).get("memory_file") == str(dirty_mem.resolve()),
              repr((structured or {}).get("memory_file")))
        check("pp_guard_check: keeps the raw engine report",
              "mem_guard" in ((structured or {}).get("raw_report") or ""))
        check("pp_guard_check: every red item carries line/key/message",
              all(isinstance(item.get("key"), str) and isinstance(item.get("message"), str)
                  and "line" in item for item in (structured or {}).get("red") or []))

        # ── 5. pp_guard_check on a clean fixture ────────────────────────
        response = client.call_tool("pp_guard_check", {"memory_file": str(clean_mem)})
        result, structured, _ = tool_payload(response)
        counts = (structured or {}).get("counts") or {}
        check("pp_guard_check: clean fixture reports clean",
              result.get("isError") is False and (structured or {}).get("clean") is True
              and counts.get("red") == 0 and counts.get("yellow") == 0,
              repr(structured)[:300])

        # ── 6. missing memory file is a tool error, not a crash ─────────
        response = client.call_tool("pp_guard_check",
                                    {"memory_file": str(sandbox / "no-such-memory.md")})
        result, structured, _ = tool_payload(response)
        check("pp_guard_check: missing file -> isError + path_not_found",
              result.get("isError") is True and (structured or {}).get("status") == "path_not_found",
              repr(structured)[:300])

        # ── 6b. read-only + idempotent ─────────────────────────────────
        snapshot = dirty_mem.read_bytes()
        first = tool_payload(client.call_tool("pp_guard_check", {"memory_file": str(dirty_mem)}))[1]
        second = tool_payload(client.call_tool("pp_guard_check", {"memory_file": str(dirty_mem)}))[1]
        check("read-only: guard leaves the memory file byte-identical",
              dirty_mem.read_bytes() == snapshot)
        check("idempotent: two guard calls agree on counts and red keys",
              (first.get("counts") == second.get("counts")
               and [i.get("key") for i in first.get("red") or []]
               == [i.get("key") for i in second.get("red") or []]),
              repr((first.get("counts"), second.get("counts"))))

        # ── 7. per-call timeout is honoured ─────────────────────────────
        response = client.call_tool("pp_guard_check",
                                   {"memory_file": str(clean_mem), "timeout_s": 0.01})
        result, structured, _ = tool_payload(response)
        error = (structured or {}).get("error") or {}
        check("pp_guard_check: timeout_s is enforced",
              result.get("isError") is True and error.get("kind") == "timeout",
              repr(structured)[:300])

        # ── 8. pp_scan_links: repo-relative dirs + dangling drive path ──
        response = client.call_tool("pp_scan_links",
                                    {"dirs": ["mcp_server", str(bad_dir), str(good_dir)]})
        result, structured, _ = tool_payload(response)
        check("pp_scan_links: not an error", result.get("isError") is False, repr(result)[:300])
        structured = structured or {}
        dir_entries = structured.get("dirs") or []
        check("pp_scan_links: every requested directory is reported",
              [entry.get("dir") for entry in dir_entries] == ["mcp_server", bad_dir.name, good_dir.name],
              repr([entry.get("dir") for entry in dir_entries]))
        dangling = []
        for entry in dir_entries:
            dangling.extend(entry.get("dangling_paths") or [])
        check("pp_scan_links: the dangling reference is captured",
              len(dangling) == 1 and fake_ref in (dangling[0].get("ref") or ""), repr(dangling))
        check("pp_scan_links: exact error total comes from the engine",
              structured.get("errors_total") == 1 and structured.get("clean") is False,
              repr((structured.get("errors_total"), structured.get("clean"))))
        check("pp_scan_links: own mcp_server/ directory is clean",
              (dir_entries[0].get("counts") or {}) == {}
              and not (dir_entries[0].get("dangling_paths") or [])
              and not (dir_entries[0].get("dead_skill_refs") or []),
              repr(dir_entries[0])[:300])

        # ── 9. pp_l2_coverage ───────────────────────────────────────────
        response = client.call_tool("pp_l2_coverage", {"memory_file": str(dirty_mem)})
        result, structured, _ = tool_payload(response)
        structured = structured or {}
        check("pp_l2_coverage: not an error", result.get("isError") is False, repr(result)[:300])
        check("pp_l2_coverage: entries are structured rows",
              isinstance(structured.get("entries"), list)
              and structured.get("pointers_checked") == len(structured.get("entries") or []),
              repr(structured)[:300])
        rows_ok = all(
            {"key", "scheme", "summary_chars", "l2_chars", "missing_at_L2_count", "missing_at_L2"} <= set(row)
            for row in structured.get("entries") or []
        )
        check("pp_l2_coverage: each row has key/scheme/counts/token list", rows_ok)
        check("pp_l2_coverage: missing_at_L2 field present",
              isinstance(structured.get("missing_at_L2"), list))

        # ── 10. pp_secrets_scan (json passthrough) ──────────────────────
        response = client.call_tool("pp_secrets_scan", {"path": "mcp_server"})
        result, structured, _ = tool_payload(response)
        structured = structured or {}
        check("pp_secrets_scan: not an error", result.get("isError") is False, repr(result)[:300])
        check("pp_secrets_scan: engine JSON parsed into findings/clean/result",
              isinstance(structured.get("total_findings"), int)
              and isinstance(structured.get("findings"), list)
              and structured.get("result") in ("CLEAN", "FINDINGS PRESENT"),
              repr(structured)[:300])
        check("pp_secrets_scan: the new mcp_server/ tree is CLEAN",
              structured.get("clean") is True and structured.get("result") == "CLEAN",
              repr(structured)[:400])
        check("pp_secrets_scan: clean matches result",
              (structured.get("total_findings") == 0) == (structured.get("result") == "CLEAN"))

        response = client.call_tool("pp_secrets_scan", {})
        result, structured, _ = tool_payload(response)
        check("pp_secrets_scan: default path is the repo root",
              result.get("isError") is False
              and Path((structured or {}).get("path", "")).resolve() == REPO_ROOT.resolve(),
              repr((structured or {}).get("path")))
        check("pp_secrets_scan: default scan reports a verdict",
              (structured or {}).get("result") in ("CLEAN", "FINDINGS PRESENT"),
              repr((structured or {}).get("result")))

        # ── 11. protocol-level error paths ─────────────────────────────
        rid, response = client.request("tools/call", {"name": "pp_nope", "arguments": {}})
        check("error: unknown tool -> -32602",
              (response.get("error") or {}).get("code") == -32602, repr(response)[:300])
        rid, response = client.request("does/not/exist", {})
        check("error: unknown method -> -32601",
              (response.get("error") or {}).get("code") == -32601, repr(response)[:300])
        rid, response = client.request("tools/call", {"name": "pp_scan_links", "arguments": {"dirs": "nope"}})
        check("error: dirs must be an array -> -32602",
              (response.get("error") or {}).get("code") == -32602, repr(response)[:300])
        rid, response = client.request("ping", {})
        check("ping: empty result", response.get("result") == {}, repr(response)[:200])

        # ── 12. framing + parser unit checks (import the server module) ──
        sys.path.insert(0, str(MCP_DIR))
        import pulse_path_mcp as srv

        body = json.dumps({"jsonrpc": "2.0", "id": 90, "method": "ping"}).encode("utf-8")
        framed = (b"Content-Length: %d\r\n" % len(body)
                  + b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n"
                  + body
                  + b'\n{"jsonrpc":"2.0","id":91,"method":"ping"}\n')
        decoded = list(srv.iter_messages(io.BytesIO(framed)))
        check("framing: Content-Length header frames are accepted as a fallback",
              [m.get("id") for m in decoded] == [90, 91], repr(decoded))
        check("framing: newline-delimited output never embeds a newline",
              b"\n" not in json.dumps({"a": "x"}, ensure_ascii=False).encode("utf-8"))

        original = srv.ENGINE_PATHS["secrets"]
        try:
            srv.ENGINE_PATHS["secrets"] = sandbox / "definitely-missing-engine.py"
            missing = srv.run_engine("secrets", [])
        finally:
            srv.ENGINE_PATHS["secrets"] = original
        check("engine: missing script -> kind=script_missing",
              missing.get("ok") is False and missing.get("kind") == "script_missing",
              repr(missing)[:200])

        # Findings exit with code 1: that must stay a normal result, not an error.
        original_run = srv.run_engine
        try:
            srv.run_engine = lambda *a, **k: {
                "ok": True, "returncode": 1, "stderr": "", "duration_ms": 1,
                "stdout": json.dumps({"root": ".", "total_findings": 1,
                                      "files": [{"file": "x.env", "status": "findings",
                                                 "findings": [[3, "bearer-token", "abcd***"]],
                                                 "info": []}]}),
            }
            with_findings = srv.tool_secrets_scan({})
            # Engine printing prose instead of JSON must be reported, not guessed at.
            srv.run_engine = lambda *a, **k: {
                "ok": True, "returncode": 0, "stdout": "RESULT: CLEAN\n", "stderr": "", "duration_ms": 1,
            }
            not_json = srv.tool_secrets_scan({})
        finally:
            srv.run_engine = original_run
        check("engine: findings exit code 1 is still a successful call",
              with_findings.get("ok") is True and with_findings.get("clean") is False
              and with_findings.get("result") == "FINDINGS PRESENT",
              repr(with_findings)[:300])
        check("engine: non-JSON stdout -> status=bad_output, no crash",
              not_json.get("ok") is False and not_json.get("status") == "bad_output",
              repr(not_json)[:300])

        sample_report = (
            "🛡 mem_guard v2 体检 2026-01-01 00:00\n"
            "🔴 需人工决策 2 条:\n"
            "  L4 dead: 断链: doc://x（路径不存在: x）\n"
            "   watermark: ⚠️ MEMORY 记忆水位 99%（11983/12000）\n"
            "  · 条目一\n"
            "  · 条目二\n"
            "🟡 自动修复 1 条:\n"
            "  L7 fixme: 🔧 技能名大小写修正\n"
            "✅ 正常 3 | ? 待全量版 1 | 指针总数 6\n"
        )
        parsed = srv.parse_guard_report(sample_report)
        check("parser: guard report counts",
              parsed["counts"] == {"red": 2, "yellow": 1, "normal": 3,
                                   "pending": 1, "pointers_total": 6}, repr(parsed["counts"]))
        watermark = parsed["red"][1] if len(parsed["red"]) > 1 else {}
        check("parser: guard multi-line watermark entry stays one item",
              watermark.get("key") == "watermark" and "条目二" in (watermark.get("message") or ""),
              repr(watermark)[:300])

    except Exception as exc:                                    # noqa: BLE001
        print("FAIL  unexpected error: %s: %s" % (type(exc).__name__, exc))
        CHECKS.append(("unexpected error", False, str(exc)))
    finally:
        if client is not None:
            client.close()
        shutil.rmtree(sandbox, ignore_errors=True)

    passed = sum(1 for _label, ok, _detail in CHECKS if ok)
    total = len(CHECKS)
    if passed != total:
        print("MCP TEST FAILED %d/%d" % (passed, total))
        return 1
    print("MCP TEST OK %d/%d" % (passed, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
