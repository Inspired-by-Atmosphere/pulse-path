# CHANGELOG

All notable changes to this repository. Format loosely follows Keep a Changelog.

## [0.2.0] - 2026-10-27 — closing the doc-vs-code gap, CI, and two ported engines

### Added
- `scripts/run_checks.py` — one offline entry point running every check (unit tests, secret scan,
  link integrity, each engine's `--selftest`, MCP protocol test). CI runs exactly this command.
- `.github/workflows/ci.yml` — 4-leg matrix (ubuntu/windows × Python 3.10/3.13), `fail-fast: false`,
  plus a step that asserts the interpreter really matches the matrix value, and quickstart runs from
  a foreign working directory on each OS.
- `examples/quickstart.sh` / `examples/quickstart.bat` — the 60-second demo, safe from any cwd
  (locates the repo root from `$0` / `%~dp0..`, builds a temp sandbox, cleans up, prints
  `QUICKSTART OK`).
- `skills/pulse-path/scripts/scan_link_graph.py` — link **graph** rather than link list: nodes, wikilinks,
  relative links, path refs, ghosts, orphans, in-degree hubs and a backlink index; `--json`, `--selftest` (27).
- `skills/memory-pointer-system/scripts/scan_archived_deps.py` — which archived skills are still
  referenced by live files, reported as `path:line`; `--selftest` (20).
- `skills/memory-pointer-system/scripts/stress_pointer_test.py` — weak-model lower-bound probe: stuff the
  context with noise, hide the pointer mid-noise, ask a question the summary cannot answer, grade by
  expected keywords. `--plan` renders the payload without network; `--selftest` (37) uses a local mock endpoint.
- `skills/memory-governance/scripts/ssot_check.py` — SSOT drift reconciliation: retired figures/phrasings
  that still linger in the live carriers, with legal-context exemption, 🔴/🟡/📌 grading and
  `--emit-workorders`; `--selftest` (26).
- `skills/memory-pointer-system/scripts/project_registry.py` — register a project plus its one-line head
  pointer; dry-run by default, `--commit` backs up first, verifies by reading back, rolls back on failure;
  `--selftest` (34).
- `mcp_server/` — zero-dependency stdio JSON-RPC MCP server exposing `pp_guard_check`, `pp_scan_links`,
  `pp_l2_coverage`, `pp_secrets_scan`; newline-delimited frames (LSP `Content-Length` accepted on input);
  `test_mcp.py` (62 assertions) and a registration README for Claude Desktop / Claude Code.
- `docs/RESULTS.md` — measured numbers (assertion counts, self-scan results, wall-clock) and method.
- `examples/facts_ssot.sample.json` — a filled SSOT rule table.
- `.gitattributes`: `*.bat text eol=crlf` so the batch file keeps the line endings `cmd.exe` expects.

### Fixed
- **`mem_guard.py` now actually enforces the summary length limit it documented.** `SKILL.md` claimed
  “summary ≤ 40 chars, over-long ones are reported” in two places while the engine had no such check
  and the unit suite had no assertion for it. The limit is declarative (`summary.max_len`,
  `summary.severity` in `mem_rules.json`), counted in characters, and covered by tests (40 ok / 41 red,
  mixed CJK+ASCII, table-driven limit and severity).
- **`cron://` and `mem://` are verified instead of “pending”.** `cron://<id|name>` resolves against
  `$HERMES_HOME/cron/jobs.json` (`MEM_GUARD_CRON` override; a missing store returns `?` rather than a
  false dangling link), and `mem://<key>` checks that the referenced pointer key exists in the same
  file. `schemes` is now 10, all verifier names registered declaratively.
- `README.md` / `README.zh-CN.md` referenced a planning document that is not part of the published
  tree — the reference is gone (a dangling reference, in the docs about dangling references).
- `README*` quickstart no longer depends on being run from the repository root; the shortcut scripts
  make that explicit.

### Changed
- `mem_guard.py`: pointer write-back now happens only when an autofix actually changed something, so a
  yellow-level notice no longer rewrites the file.
- `README.md` / `README.zh-CN.md`: added an “Engines at a glance” table, a “Results (measured)”
  section, the new environment variables, and limitations that state plainly what this repo does
  **not** do (no retrieval, no vector store; probe needs a model; MCP server is a thin wrapper).
- `CHANGELOG` version stays pre-1.0: the methodology is stable, the engines are reference
  implementations.

## [0.1.0] - 2026-09-28 — first public release (sanitized superset revision)

### Added
- `README.zh-CN.md` (Chinese README, aligned with the English one).
- `LICENSE` (MIT), `.gitignore`, `requirements.txt`, `CHANGELOG.md`.
- `scripts/check_secrets.py` — zero-dependency pre-publish scanner (credentials, private keys,
  emails, private IPs/MACs, high-entropy strings, local absolute paths).
- `docs/SANITIZE_LOG.md` — category-level record of every de-identification action.
- A file-by-file plan of this revision against the previous public one — **kept out of the published
  tree on purpose** (it names internal decisions); the shipped result is this changelog plus the code.
- `examples/MEMORY.sample.md` — runnable pointer-dictionary fixture.
- `examples/mem_rules.sample.json` — rule-table starting point.
- **New skill `memory-governance/`** with four references: `consolidation-engine.md`,
  `curator-ticket-pipeline.md`, `memory-chain-diagnosis.md`, `events-governance.md`.
- **New references under `memory-pointer-system/`:** `ghost-scan-v2.md` (previously referenced by
  `SKILL.md` but absent from the repo → dangling pointer), `governance-engines.md`.

### Changed
- **All eight engines are now machine-portable.** Every hard-coded absolute path was replaced by an
  environment variable with a portable default (`HERMES_HOME`, `MEM_GUARD_*`, `AUDIT_*`,
  `CONSOLIDATE_*`, `CURATE_REPORT`, `VIKING_ROOT`, `OV_CLI`, `GOVERNANCE_MODULE_DIR`). See the
  configuration table in `README.md`.
- `mem_guard.py`, `auditor.py`, `consolidator.py`, `scan_links.py`, `scan_ghost_script_refs.py`
  gained `--help`; `check_l2_coverage.py` gained `--help` and an optional positional file argument;
  missing input files now produce a hint instead of a traceback.
- `mem_guard.py` resolves `~` / `$VAR` / `HERMES_HOME`-relative paths from the rule table.
- `consolidator.py`: the project-name exemption in the category-misplacement check is now declarative
  (`consolidation.project_prefixes`) instead of a hard-coded string.
- `curator.py`: the optional governance-module import is driven by `GOVERNANCE_MODULE_DIR` and off by
  default (previously a hard-coded skill path); report output creates its directory.
- `scan_ghost_script_refs.py` was restructured from an import-time script into a `main()` with CLI
  flags (`--docs-root/--zone/--plan-zone/--search-root/--skip`), all directories configurable; no
  defaults are baked in.
- `mem_rules.json`: all absolute paths replaced with `${HERMES_HOME}` expressions; the ghost-scan
  block ships empty + `enabled: false` (user configures); a user-name token was dropped from
  `stopwords`.
- `test_mem_guard.py` rewritten to be **self-contained** (temp sandbox fixtures, no dependence on the
  author's machine). The previous suite asserted against real local paths and against a stale
  watermark `limit`, so it failed 1/25 even on the author's machine — now 26/26.
- `SKILL.md` for all three skills: local paths, private repository references, personal identifiers,
  internal project names and internal figures removed or genericised.
- `README.md` rewritten as the English primary doc (positioning, layout, ≤3-step quickstart, full
  environment-variable table, limitations, license).
- `.gitattributes` extended to `.json` / `.yml` / `.yaml` / `.sh` (LF), fixing spurious whole-file
  diffs caused by `core.autocrlf`.

### Removed
- Hard-coded absolute paths in every script and in `mem_rules.json` (see "Changed").
- Internal project names, team/competition references and the private mirror URL from
  `README.md` and `memory-pointer-system/SKILL.md`.
- Internal metrics and case-study figures from `pulse-path/SKILL.md` and
  `memory-pointer-system/SKILL.md` (kept the method, dropped the numbers).
- Personal hardware/GPU model references and machine-manager document references from the
  memory-governance material.
- The `__pycache__/` artifact that had been committed alongside the scripts; now git-ignored.

### Fixed
- `HERMES_HOME` is normalised to an absolute path and written back to the environment in all seven
  scripts. With a relative `HERMES_HOME` (e.g. `HERMES_HOME=./sandbox` for a throwaway demo), the
  `${HERMES_HOME}/...` entries in `mem_rules.json` expanded to a relative path that was then joined
  to `HERMES_HOME` a second time — the watermark check reported `MEMORY/USER 读取失败` against a
  path that could never exist. Found while writing the quickstart; `test_mem_guard.py` still 26/26.
- `README.md` / `README.zh-CN.md`: quickstart replaced with a 60-second sandboxed demo, including the
  real command output, so the first thing a visitor sees is what the guard actually catches.

### Notes
- Gate results (S1–S5) and the exact commands are recorded in the maintainer's internal working notes,
  which are deliberately not part of this repository (referencing them here would create exactly the
  dangling pointer this project exists to prevent).
