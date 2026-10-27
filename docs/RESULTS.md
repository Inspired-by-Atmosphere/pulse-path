# Results (measured)

This file records **what was actually run** for this revision, so claims in the README can be checked
instead of trusted. Everything below is reproducible with one command.

```bash
python scripts/run_checks.py        # runs every check listed here
```

## Environment

| | |
|---|---|
| OS | Windows 11 (build 26200), git-bash |
| Python | 3.14.9 (CPython, 64-bit) |
| CPU/disk | laptop, single SSD, no network needed |
| Third-party packages | **none** (standard library only) |

CI re-runs the same command on ubuntu-latest and windows-latest with Python 3.10 and 3.13
(`.github/workflows/ci.yml`, 4 legs, `fail-fast: false`). The matrix leg asserts its own interpreter
version, so "it passed on the wrong Python" cannot happen silently.

## Suites and assertion counts

| Suite | Assertions | Result |
|---|---|---|
| `skills/memory-pointer-system/scripts/test_mem_guard.py` | 62 | 62 pass / 0 fail |
| `skills/memory-pointer-system/scripts/scan_archived_deps.py --selftest` | 20 | 20/20 |
| `skills/pulse-path/scripts/scan_link_graph.py --selftest` | 27 | 27/27 |
| `skills/memory-pointer-system/scripts/stress_pointer_test.py --selftest` | 37 | 37/37 |
| `skills/memory-governance/scripts/ssot_check.py --selftest` | 26 | 26/26 |
| `skills/memory-pointer-system/scripts/project_registry.py --selftest` | 34 | 34/34 |
| `skills/memory-pointer-system/scripts/pp_index.py --selftest` | 35 | 35/35 |
| `examples/mcp_client_example.py --selftest` | 5 | 5/5 |
| `mcp_server/test_mcp.py` | 76 | 76/76 |
| **Total** | **322** | all pass |

Every suite builds its own fixtures under a temporary directory and needs no network, no API key and
no file outside the repository. The weak-model probe uses a local `http.server` mock for its network
path; the real endpoint is only touched when you configure one.

## The repository's own self-scan

| Check | Result |
|---|---|
| `scripts/check_secrets.py .` | `scanned 49 file(s); findings: 0` → `CLEAN` |
| `skills/pulse-path/scripts/scan_links.py skills` | `✅ 无失效引用` (no dangling references) |
| `skills/pulse-path/scripts/scan_link_graph.py .` | 2 missing targets in the published tree, both illustrative (see below) |

The link graph is not asserted to be zero: it reports what it finds, and the residue is content, not
breakage — a client-config path and a training-script path appear inside illustrative text in one
reference document (they are examples of what a ghost link looks like). Keeping them visible is the
point; suppressing them would be the "clean scan" lie.

## Wall-clock (single run per row, warm cache unless noted)

| Command | Time |
|---|---|
| `mem_guard.py --check` (5-pointer fixture) | ≈0.37 s |
| `pp_index.py --build` (19 markdown files, 156 KB of body text) | ≈0.06 s |
| `pp_index.py --search` **as a CLI call** | ≈0.45–0.7 s |
| the same FTS query **in-process** | 0.3–1.5 ms |
| `scan_link_graph.py .` (whole repo) | ≈0.59 s |
| `scan_links.py skills` | ≈0.60 s |
| MCP `initialize` + one `tools/call pp_guard_check` | ≈0.65 s round trip |
| `scripts/run_checks.py` (all of the above, serial) | a few seconds |

The gap between the CLI row and the in-process row is **Python interpreter startup**, not the index.
That is worth saying out loud: a batch of queries from one process is ~1000× cheaper than one CLI call
per query, which is why the MCP tool and any agent integration should keep the process warm.

These are wall-clock on a laptop with no averaging — treat them as order of magnitude, not as a
benchmark. The numbers that should gate a release are the assertion counts and the CLEAN / dangling
results, because those are deterministic.

## What is **not** measured here (deliberate)

- **No claim that this makes an agent smarter.** There is no benchmark number for "memory quality"
  attached to this repo, and inventing one from a single project's anecdote would be exactly the kind
  of claim that gets an entry removed from a curated list.
- **No comparison table against other memory projects.** They store and retrieve memory; this
  validates the reference graph of memory files. A star count is not a measurement of either.
- **No retrieval-quality number for `pp_index`.** There is no recall@k or nDCG here, because that needs
  a labelled query set this project does not have; inventing one from a handful of greps would be
  theatre. What is measured is that Chinese and English queries return the expected documents, that
  zero-hit queries return `hits: 0` instead of an error, and that rebuilds are incremental.
- **No host-side token accounting.** The pointer dictionary costs what your agent charges to inject
  it; this repo measures the *engines*, not your context window.
- **No multi-run statistics.** One run per row, on one machine. Variance is real (0.36–0.9 s for the
  same command depending on cache state) and is not hidden behind a "+/-".

## Reproducing on another machine

```bash
# 1) whole suite, no network, no keys
python scripts/run_checks.py

# 2) the numbers in the timing table
HERMES_HOME=/tmp/pp \
  python skills/memory-pointer-system/scripts/mem_guard.py --check --verbose
python skills/pulse-path/scripts/scan_link_graph.py .
python skills/pulse-path/scripts/scan_links.py skills

# 3) the weak-model probe, once you point it at any OpenAI-compatible endpoint
PP_STRESS_BASE_URL=http://127.0.0.1:11434/v1 PP_STRESS_MODEL=qwen3:8b \
  python skills/memory-pointer-system/scripts/stress_pointer_test.py --plan   # render only
```

If a number here does not reproduce on your machine, that is a bug report worth filing — include
`python -VV` and the full command output.
