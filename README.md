# pulse-path

[![CI](https://github.com/Inspired-by-Atmosphere/pulse-path/actions/workflows/ci.yml/badge.svg)](https://github.com/Inspired-by-Atmosphere/pulse-path/actions/workflows/ci.yml)

> A linked-list discipline for managing knowledge, memory and reference systems in large projects.
> 大型项目知识/记忆/引用体系按**链表**组织与维护：删除重接链、修改查指针、新增带出链、整理后一致性扫描。

- English (this file) · [中文版](README.zh-CN.md)

## Why / What this is

Big projects (many contributors, many documents, many skills, long lifetime) tend to have their
knowledge assets **torn apart** by ordinary edits: you delete a file or rename a path, and the
documents that referenced it silently go stale. You only notice later, by hand.

`pulse-path` treats a knowledge base as a **linked list**: every memory entry is a node, every
"see also / points to / references" is a pointer, and no edit may leave a dangling pointer.

Five laws:

1. **Delete = relink** — before removing a node, find every pointer to it; redirect or delete
   them together. No dangling pointers.
2. **Modify = check both directions** — inbound links (who references me, is it still true?)
   and outbound links (is what I point to still reachable?).
3. **Add = declare the destination** — a new node must carry at least one outbound link.
   No islands.
4. **Always verify after reorganizing** — path existence plus multi-source cross-checks.
5. **Uniform treatment** — built-in memory, fact stores, skills, `AGENTS.md`, docs: all the same.

Plus **layered activation**: L1 index layer (short, always-visible pointers) → L2 content layer
(fact store / skills / docs / session history), with an "empty index ≠ information missing" guard,
so project memory is pulled in on demand instead of occupying every context window.

## Layout

```
pulse-path/
├── README.md / README.zh-CN.md
├── CHANGELOG.md · LICENSE · requirements.txt
├── .github/workflows/ci.yml                 # 4 legs: ubuntu/windows × Python 3.10/3.13
├── scripts/
│   ├── check_secrets.py                     # zero-dependency pre-publish secret scan
│   └── run_checks.py                        # one entry point: every offline check
├── mcp_server/
│   ├── pulse_path_mcp.py                    # stdio JSON-RPC MCP server (stdlib only)
│   ├── test_mcp.py                          # 62-assertion protocol test (offline)
│   └── README.md
├── docs/SANITIZE_LOG.md · docs/RESULTS.md
├── examples/
│   ├── MEMORY.sample.md                     # a small pointer dictionary
│   ├── mem_rules.sample.json                # rule-table starting point
│   ├── facts_ssot.sample.json               # filled SSOT-reconciliation rules
│   └── quickstart.sh / quickstart.bat       # 60-second demo, safe from any cwd
└── skills/
    ├── pulse-path/                          # methodology layer: the five laws
    │   ├── SKILL.md
    │   ├── references/classification_rules.md   # information type → single owning carrier
    │   └── scripts/{scan_links,scan_link_graph}.py
    ├── memory-pointer-system/               # implementation layer: how to write & verify pointers
    │   ├── SKILL.md
    │   ├── references/{ghost-scan-v2.md, governance-engines.md}
    │   ├── scripts/{mem_guard,auditor,consolidator,curator,memory_request,
    │   │            check_l2_coverage,scan_ghost_script_refs,test_mem_guard,
    │   │            scan_archived_deps,stress_pointer_test,project_registry}.py
    │   └── scripts/mem_rules.json           # declarative rule table (no business `if`s in engines)
    └── memory-governance/                   # governance layer: layering, demotion, write pipeline
        ├── SKILL.md
        ├── references/{consolidation-engine,curator-ticket-pipeline,
        │               memory-chain-diagnosis,events-governance}.md
        └── scripts/ssot_check.py            # SSOT drift reconciliation
```

The three skills are drop-in agent skills (`.claude/skills/`, a Hermes `skills/` directory, or any
loader that reads a `SKILL.md` with YAML front matter). They are usable independently — most teams
will only want `pulse-path` plus `scan_links.py`.

## Engines at a glance

Every engine is standard-library Python, reads its thresholds from `mem_rules.json` where a threshold
makes sense, and prints for humans unless you pass `--json`.

| Engine | What it answers | Exit code |
|---|---|---|
| `scan_links.py` | Does every path/skill reference in these docs still resolve? | 0 clean / 1 dangling |
| `scan_link_graph.py` | What is the shape of the doc graph — ghosts, orphans, backlinks, hubs? | always 0 |
| `mem_guard.py` | Is every pointer well-formed, does its target exist, is the summary under the limit, is the file over the watermark? | always 0 |
| `check_l2_coverage.py` | Before shortening a pointer summary: is every dropped token already at its L2 target? | always 0 |
| `auditor.py` | Content-level audit: classification-law compliance, cross-carrier duplication, ticket→store chain. | always 0 |
| `consolidator.py` | Sleep-cycle detection: watermark, demotion candidates, duplicates, staleness, category drift. | always 0 |
| `ssot_check.py` | Do retired figures/phrasings still linger in the live carriers (SSOT drift)? | always 0 |
| `curator.py` | The single writer: drain the ticket queue through quality gates into the store. | always 0 |
| `memory_request.py` | Session side: file a ticket instead of writing memory directly. | always 0 |
| `scan_ghost_script_refs.py` | Do the scripts these docs mention actually exist anywhere? | always 0 |
| `scan_archived_deps.py` | Which archived skills are still referenced by live files? | always 0 |
| `stress_pointer_test.py` | Under a stuffed context, can even a weak model still resolve a pointer correctly? | 0 pass / 1 fail / 2 setup error |
| `project_registry.py` | Register a project and its one-line head pointer (dry-run by default). | 0 / 1 error |
| `mcp_server/pulse_path_mcp.py` | The same checks as MCP tools over stdio, for any MCP client. | JSON-RPC |

**Every check at once, offline:** `python scripts/run_checks.py` (this is what CI runs).

## Quick start (60 seconds)

Python 3 standard library only — no install, no dependencies. Point the guard at a
throwaway copy of the fixtures and watch it catch broken pointers:

```bash
SANDBOX=/tmp/pp-demo                       # any throwaway directory
mkdir -p "$SANDBOX/memories"
cp examples/MEMORY.sample.md "$SANDBOX/memories/MEMORY.md"
printf '# User profile\n- Demo line\n' > "$SANDBOX/memories/USER.md"

HERMES_HOME="$SANDBOX" python skills/memory-pointer-system/scripts/mem_guard.py --check --verbose
SKILLS_ROOT=skills     python skills/pulse-path/scripts/scan_links.py skills
```

`--check` is a dry run: it reports and exits, it never rewrites the pointer file.
Verbatim output of those two commands (`$SANDBOX` stands for whatever directory you
used; the tool reports in Chinese):

```text
🛡 mem_guard v2 体检 2026-09-28 10:13
🔴 需人工决策 4 条:
  L14 pp: 断链: skill://pulse-path（技能 pulse-path 不存在于 $SANDBOX\skills）
  L15 mps: 断链: skill://memory-pointer-system（技能 memory-pointer-system 不存在于 $SANDBOX\skills）
  L17 gone: 断链: doc://examples/does-not-exist.md（路径不存在: examples/does-not-exist.md）
  L18 svc: 断链: cfg://SAMPLE_SERVICE_KEY（.env 不存在）
✅ 正常 1 | ? 待全量版 0 | 指针总数 5

技能库索引: 6 个技能名

✅ 无失效引用
```

The `L14`/`L15` lines are expected: the sandbox has no `skills/` directory, which is
exactly the class of mistake the guard exists to catch. `L17`/`L18` are the two
dangling pointers the sample fixture ships on purpose.

Shortcut: `bash examples/quickstart.sh` — or `examples\quickstart.bat` on Windows — performs the same
demo from any working directory, then runs the full offline self-check. `python scripts/run_checks.py`
runs the checks alone (unit tests, secret scan, link integrity, every engine's `--selftest`, and the
MCP protocol test); that single command is what CI executes.

Then wire it into your own project:

1. Copy `skills/pulse-path/` (or all three) into your agent's skill directory.
2. Run the link-integrity check over your docs after any reorganization:

```bash
python skills/pulse-path/scripts/scan_links.py <dir...>     # exit 0 = clean, 1 = dangling refs
python skills/pulse-path/scripts/scan_links.py --help       # env vars & options
```

3. (Optional) Add the memory guard: point it at your pointer file and run a dry-run health check.

```bash
# HERMES_HOME defaults to ~/.hermes; MEMORY_FILE defaults to $HERMES_HOME/memories/MEMORY.md
python skills/memory-pointer-system/scripts/mem_guard.py --check --verbose
python skills/memory-pointer-system/scripts/test_mem_guard.py     # self-contained unit tests
```

## Configuration

Every script resolves paths from environment variables with portable, personal-data-free defaults.

| Variable | Used by | Default | Meaning |
|---|---|---|---|
| `HERMES_HOME` | all | `~/.hermes` | root of the memory tree |
| `SKILLS_ROOT` | `scan_links.py` | `$HERMES_HOME/skills` | where skills live (basis for `skill://` refs) |
| `SCAN_DIRS` | `scan_links.py` | `$SKILLS_ROOT` + cwd | default scan targets (`os.pathsep`-separated) |
| `MEM_GUARD_MEMORY` | `mem_guard.py` | `$HERMES_HOME/memories/MEMORY.md` | pointer file to audit |
| `MEM_GUARD_RULES` | `mem_guard.py` | `mem_rules.json` next to the script | declarative rule table |
| `MEM_GUARD_SKILLS` / `MEM_GUARD_SCRIPTS` | `mem_guard.py` | `$HERMES_HOME/skills` / `$HERMES_HOME/scripts` | basis for `skill://` and `script://` |
| `MEM_GUARD_ENV` / `MEM_GUARD_CONFIG` | `mem_guard.py` | `$HERMES_HOME/.env` / `config.yaml` | basis for `cfg://` / `env://` |
| `AUDIT_MEMORY` / `AUDIT_USER` / `AUDIT_FACT_DB` | `auditor.py` | under `$HERMES_HOME` | content-level audit inputs |
| `AUDIT_SKILLS` / `AUDIT_REPORT` | `auditor.py` | `$HERMES_HOME/{skills,reports}` | skill root / report output |
| `CONSOLIDATE_MEMORY` / `CONSOLIDATE_USER` | `consolidator.py` | under `$HERMES_HOME` | consolidation inputs |
| `CONSOLIDATE_RULES_REF` | `consolidator.py` | `../pulse-path/references/classification_rules.md` | classification table |
| `CONSOLIDATE_REPORT` / `CURATE_REPORT` | engines | `$HERMES_HOME/reports/` | report output |
| `VIKING_ROOT` | auditor / curator / `check_l2_coverage.py` | `~/.openviking/data/viking/default/…` | semantic store root (optional) |
| `OV_CLI` | `curator.py` | `$HERMES_HOME/bin/ov.exe` | semantic-store CLI (optional; dry-run works without it) |
| `GOVERNANCE_MODULE_DIR` | `curator.py` | *(unset)* | optional external approval/decision engine |
| `MEM_GUARD_CRON` | `mem_guard.py` | `$HERMES_HOME/cron/jobs.json` | job store used to verify `cron://` pointers (absent ⇒ `?`, not a false alarm) |
| `SSOT_RULES` | `ssot_check.py` | `memory-pointer-system/scripts/mem_rules.json` | rule table holding the `facts_ssot` block |
| `PROJECT_RULES` / `PROJECT_MEMORY` / `PROJECT_SKILLS` | `project_registry.py` | rule table next to the script / `$HERMES_HOME/memories/MEMORY.md` / `$HERMES_HOME/skills` | where registration lands |
| `ARCH_SCAN_ROOTS` | `scan_archived_deps.py` | `$HERMES_HOME` + cwd | default search roots (`os.pathsep`-separated) |
| `PP_STRESS_BASE_URL` / `PP_STRESS_API_KEY` / `PP_STRESS_MODEL` | `stress_pointer_test.py` | *(unset)* | OpenAI-compatible endpoint, key, model for the weak-model probe |
| `PULSE_PATH_MCP_TIMEOUT` | `mcp_server/pulse_path_mcp.py` | `120` (seconds) | per-tool-call timeout for the wrapped engines |
| `DOCS_HOME` | `auditor.py` | `~/Documents` | where relative doc references are resolved |

`mem_rules.json` may reference paths as `~`, `$VAR`, or relative to `HERMES_HOME`.

Optionally install these as scheduled jobs (daily health check, nightly consolidation, weekly audit).
Detection scripts **always exit 0** and report through stdout — see
`skills/memory-governance/references/consolidation-engine.md` for why, and for alert-level calibration.

## Results (measured)

Windows 11, Python 3.14.9, one laptop. Reproduce everything with `python scripts/run_checks.py`:

| Measurement | Value |
|---|---|
| Offline checks in `run_checks.py` | **9/9 pass** |
| Assertions across the suites | 47 unit + 20 + 27 + 37 + 26 + 34 (engine selftests) + 62 (MCP protocol) |
| The repo's own secret scan | `CLEAN` |
| The repo's own link-integrity scan | `0` dangling references |
| `mem_guard.py --check` (5-pointer fixture) | ~0.37 s |
| `scan_link_graph.py` (whole repo) | ~0.59 s |
| `scan_links.py` (repo skills) | ~0.60 s |
| MCP `initialize` + one `tools/call` | ~0.65 s round trip |

Wall-clock numbers move with disk cache and file count; the assertion counts and the CLEAN/dangling
results are the ones to hold the project to. Method and caveats: `docs/RESULTS.md`.

## Example pointer dictionary

```
§
mem_guard = skill://memory-pointer-system | 指针体检引擎：格式/断链/水位
rules     = doc://docs/classification.md | 信息类型→唯一载体归属表
jobs      = cron://nightly-audit | 每夜审计任务；异常经 stdout 上报
api       = cfg://SERVICE_API_KEY | 取值见 .env，勿写入记忆正文
```

`examples/MEMORY.sample.md` is a runnable fixture for `mem_guard.py --check`.

## Design notes / limitations

- **Methodology first, engines second.** The five laws are the valuable part; the engines are one
  reference implementation. Nothing here depends on a specific agent runtime.
- **Semantic-store integration is optional.** `viking://` pointers, `ov`-based curation and event
  governance assume an OpenViking-like store with a CLI. Without one, the guard / consolidation /
  report paths still work; `curator --commit` simply will not write anywhere.
- **Not a database.** The pointer dictionary is a plain markdown file; concurrency control is the
  host agent's memory tool, not this repo.
- **Watermark check compares against the `limit`s you configure** (`mem_rules.json`). If your host
  raises its memory character limit, update the rule table, or the percentage will be wrong.
  (A previous revision shipped a stale limit and its own test suite consequently failed 1/25.)
- **Windows / git-bash note:** run the scripts with `python`, and pass native paths (`<DRIVE>:/path/to/script.py`)
  rather than MSYS paths (`/c/...`) — MSYS path translation is commonly disabled.
- **Nothing here retrieves memory.** There is no embedding, index or vector store: a pointer is
  resolved by the host agent, which reads the summary line and opens the target itself. This repo
  validates the *graph*, not the retrieval — it complements a memory store, it does not replace one.
- **The weak-model probe needs a model.** `stress_pointer_test.py` speaks the OpenAI-compatible
  `/chat/completions` protocol; with no endpoint configured it can only render the prompt (`--plan`).
  It shows that a weak model *can* still resolve a pointer — not that yours will.
- **The MCP server is a thin wrapper.** It parses the engines' human-readable reports; if a report
  format changes the wrapper degrades (raw text kept, warning attached) instead of inventing a
  result. Every tool is read-only and calls `--check`.

## Provenance

Distilled from real use on a long-running, multi-contributor project. Names, organisations, paths and
internal figures have been removed — see `docs/SANITIZE_LOG.md`; the measured numbers for this
revision are in `docs/RESULTS.md`.

## License

MIT — see `LICENSE`.
