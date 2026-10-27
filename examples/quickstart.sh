#!/usr/bin/env bash
# pulse-path — 60-second demo, runnable from ANY working directory.
#
#   bash examples/quickstart.sh          (or ./examples/quickstart.sh)
#
# What it does:
#   1. locates the repository root from this script's own location
#   2. builds a throwaway sandbox in the system temp dir and copies the
#      example pointer file into it
#   3. runs mem_guard.py --check --verbose  (dry run: reports, never rewrites)
#   4. runs scan_links.py over skills/
#   5. runs scripts/run_checks.py          (the unified self-check entry point)
#
# The two dangling pointers reported by mem_guard in step 3 are intentional:
# examples/MEMORY.sample.md ships them so the demo shows the guard catching
# broken links. The sandbox is deleted on exit.
#
# Override the interpreter with:  PYTHON=/path/to/python bash examples/quickstart.sh

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)" || { echo "ERROR: cannot resolve repo root"; exit 1; }
cd "$ROOT" || { echo "ERROR: cannot cd to $ROOT"; exit 1; }

# ── pick an interpreter ──
# A candidate must actually execute: on Windows, `python3` often resolves to the
# Microsoft Store app-execution-alias stub, which exists on PATH but exits 49.
PY=""
_probe() { "$1" -c 'import sys; assert sys.version_info[0] == 3' >/dev/null 2>&1; }
if [ -n "${PYTHON:-}" ]; then
  if _probe "$PYTHON"; then PY="$PYTHON"; else
    echo "ERROR: PYTHON=$PYTHON is not a working Python 3 interpreter"; exit 1
  fi
else
  for cand in python3 python py; do
    if command -v "$cand" >/dev/null 2>&1 && _probe "$cand"; then PY="$cand"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "ERROR: no working Python 3 interpreter on PATH (tried: python3 python py; set PYTHON=... to override)"
  exit 1
fi

echo "== pulse-path quickstart =="
echo "repo root : $ROOT"
echo "python    : $PY ($("$PY" -c 'import sys;print(sys.version.split()[0])' 2>/dev/null || echo '?'))"

# ── throwaway sandbox ──
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/pp-quickstart-XXXXXX")" || {
  echo "ERROR: cannot create a temp directory"; exit 1; }
cleanup() { [ -n "${SANDBOX:-}" ] && rm -rf "$SANDBOX"; }
trap cleanup EXIT INT TERM

# Native Windows python cannot read MSYS-style paths (/tmp/..., /c/...), so hand
# it a native path when cygpath is available (git-bash / WSL interop).
HOME_NATIVE="$SANDBOX"
if command -v cygpath >/dev/null 2>&1; then
  HOME_NATIVE="$(cygpath -w "$SANDBOX")"
fi
echo "sandbox   : $HOME_NATIVE"
echo

mkdir -p "$SANDBOX/memories" || { echo "ERROR: cannot create $SANDBOX/memories"; exit 1; }
cp examples/MEMORY.sample.md "$SANDBOX/memories/MEMORY.md" || { echo "ERROR: copy fixture failed"; exit 1; }
printf '# User profile\n- Demo line\n' > "$SANDBOX/memories/USER.md" || { echo "ERROR: write USER.md failed"; exit 1; }

FAILED=0

echo "── 1/3 mem_guard --check --verbose (dry run) ──"
HERMES_HOME="$HOME_NATIVE" "$PY" skills/memory-pointer-system/scripts/mem_guard.py --check --verbose
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "  -> mem_guard exited $rc"; FAILED=1
fi
echo

echo "── 2/3 scan_links.py skills ──"
SKILLS_ROOT=skills "$PY" skills/pulse-path/scripts/scan_links.py skills
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "  -> scan_links exited $rc"; FAILED=1
fi
echo

echo "── 3/3 scripts/run_checks.py (unified self-check) ──"
"$PY" scripts/run_checks.py
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "  -> run_checks exited $rc"; FAILED=1
fi
echo

if [ "$FAILED" -ne 0 ]; then
  echo "QUICKSTART FAILED — see the failing step above"
  exit 1
fi
echo "QUICKSTART OK"
