# CHANGELOG

All notable changes to this repository. Format loosely follows Keep a Changelog.

## [Unreleased] — sanitization + superset revision (staging, not pushed)

### Added
- `README.zh-CN.md` (Chinese README, aligned with the English one).
- `LICENSE` (MIT), `.gitignore`, `requirements.txt`, `CHANGELOG.md`.
- `scripts/check_secrets.py` — zero-dependency pre-publish scanner (credentials, private keys,
  emails, private IPs/MACs, high-entropy strings, local absolute paths).
- `docs/SANITIZE_LOG.md` — category-level record of every de-identification action.
- `docs/DIFF_PLAN.md` — file-by-file plan of this revision vs. the previous public revision.
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

### Notes
- Nothing was pushed. No remote was created. The upstream working copy was not modified.
- Gate results (S1–S5) and the exact commands are recorded in `docs/DIFF_PLAN.md` §5.
