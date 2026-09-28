"""Pre-flight coverage check before shrinking a pointer summary or converting a narrative entry.

Rule it enforces (pulse-path): a summary is the SAFETY NET — anything you delete from it must
already exist at the pointer's L2 target, or the memory breaks. This script extracts the
distinctive tokens of an over-long summary (cron ids, *.py names, absolute paths, numbers) and
greps the L2 text for each, so you only drop tokens that are provably covered.

Usage:  python check_l2_coverage.py           # scans $HERMES_HOME/memories/MEMORY.md
        python check_l2_coverage.py <file.md>
Output: per-pointer line -> "missing_at_L2=[...]" (keep those tokens in the new summary).

Notes / real findings (2026-09-19 run):
- Tokens absent at L2 were sometimes STALE, not missing: `15ced01d290a` (water-level cron) had
  been deleted months earlier -> dropping it was the correct fix, not keeping it.
- A token may live in a DIFFERENT L2 than the pointer target (git `http.version` tip lives in
  hermes-troubleshooting while the pointer targets hermes-agent) — acceptable, but disclose it.
- viking:// targets are semantic search terms, not file paths: the check reports them as
  unresolvable, so verify those with a real semantic search instead.
"""

import io
import re
from pathlib import Path
import os

HOME = Path(os.path.abspath(os.path.expanduser(os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes"))))
os.environ["HERMES_HOME"] = str(HOME)
SK = HOME / "skills"
VIKING = Path(os.path.expanduser(os.environ.get("VIKING_ROOT")
                                 or os.path.join("~", ".openviking", "data", "viking", "default")))

PAT = re.compile(r"^([a-z_][a-z0-9_]*) = ([a-z]+)://(\S*) \| (.+)$", re.S)
TOKEN_RES = [
    ("cron", re.compile(r"\b[0-9a-f]{12}\b")),
    ("script", re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.py\b")),
    ("path", re.compile(r"[A-Z]:\\[^\s，,；;）)]+")),
    ("num", re.compile(r"\b\d+(?:\.\d+)?(?:[A-Za-z%★]{0,2})\b")),
]


def l2_text(scheme, target):
    """Best-effort text of the pointer's content layer ('' when not resolvable)."""
    if scheme == "skill":
        name = target.split()[0]
        chunks = []
        for d in [p for p in SK.rglob(name) if p.is_dir()][:1]:
            for f in d.rglob("*"):
                if f.is_file() and f.suffix in (".md", ".py", ".json", ".txt", ".yaml"):
                    chunks.append(io.open(f, encoding="utf-8", errors="ignore").read())
        return "\n".join(chunks)
    if scheme == "doc":
        p = Path(target)
        if p.is_file():
            return io.open(p, encoding="utf-8", errors="ignore").read()
        if p.is_dir():
            return "\n".join(io.open(f, encoding="utf-8", errors="ignore").read()
                             for f in p.rglob("*") if f.is_file() and f.suffix in (".md", ".txt", ".py", ".json"))
        return ""
    if scheme == "viking":
        rest = target.replace("viking://", "").lstrip("/")
        cand = VIKING / rest if rest.startswith(("user/", "agent/", "resources/", "session/")) else None
        if cand and cand.is_file():
            return io.open(cand, encoding="utf-8", errors="ignore").read()
        return ""
    return ""


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Pre-flight L2 coverage check before shrinking a pointer summary "
                    "(pulse-path): every token you drop from a summary must already exist at the "
                    "pointer's L2 target.")
    ap.add_argument("memory_file", nargs="?",
                    help="pointer/memory markdown file (default: $HERMES_HOME/memories/MEMORY.md)")
    args = ap.parse_args()
    mem = Path(args.memory_file) if args.memory_file else HOME / "memories" / "MEMORY.md"
    if not mem.exists():
        print(f"memory file not found: {mem}\n"
              f"set HERMES_HOME, or pass the path as the first argument (see --help)")
        return 0
    try:
        txt = io.open(mem, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print(f"cannot read {mem}: {e}")
        return 0
    for s in [x.strip() for x in txt.split("\u00a7") if x.strip()]:
        m = PAT.match(s)
        if not m:
            continue
        key, scheme, target, summary = m.groups()
        if len(summary.strip()) <= 40:
            continue
        body = l2_text(scheme, target)
        tokens = [t for kind, rx in TOKEN_RES for t in rx.findall(summary)]
        missing = [t for t in tokens if t not in body]
        print(f"{key} [{scheme}] summary={len(summary.strip())} L2chars={len(body)} missing_at_L2={len(missing)} -> {missing[:12]}")


if __name__ == "__main__":
    main()
