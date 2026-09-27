# pulse-path

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
├── scripts/check_secrets.py                 # zero-dependency pre-publish secret scan
├── docs/{SANITIZE_LOG.md, DIFF_PLAN.md}
├── examples/
│   ├── MEMORY.sample.md                     # a small pointer dictionary
│   └── mem_rules.sample.json                # rule-table starting point
└── skills/
    ├── pulse-path/                          # methodology layer: the five laws
    │   ├── SKILL.md
    │   ├── references/classification_rules.md   # information type → single owning carrier
    │   └── scripts/scan_links.py                # link-integrity check (paths + skill refs)
    ├── memory-pointer-system/               # implementation layer: how to write & verify pointers
    │   ├── SKILL.md
    │   ├── references/{ghost-scan-v2.md, governance-engines.md}
    │   ├── scripts/{mem_guard,auditor,consolidator,curator,memory_request,
    │   │            check_l2_coverage,scan_ghost_script_refs,test_mem_guard}.py
    │   └── scripts/mem_rules.json           # declarative rule table (no business `if`s in engines)
    └── memory-governance/                   # governance layer: layering, demotion, write pipeline
        ├── SKILL.md
        └── references/{consolidation-engine,curator-ticket-pipeline,
                        memory-chain-diagnosis,events-governance}.md
```

The three skills are drop-in agent skills (`.claude/skills/`, a Hermes `skills/` directory, or any
loader that reads a `SKILL.md` with YAML front matter). They are usable independently — most teams
will only want `pulse-path` plus `scan_links.py`.

## Quickstart

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

`mem_rules.json` may reference paths as `~`, `$VAR`, or relative to `HERMES_HOME`.

Optionally install these as scheduled jobs (daily health check, nightly consolidation, weekly audit).
Detection scripts **always exit 0** and report through stdout — see
`skills/memory-governance/references/consolidation-engine.md` for why, and for alert-level calibration.

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
- **Windows / git-bash note:** run the scripts with `python`, and pass native paths (`C:/...`)
  rather than MSYS paths (`/c/...`) — MSYS path translation is commonly disabled.

## Provenance

Distilled from real use on a long-running, multi-contributor project. Names, organisations, paths and
internal figures have been removed — see `docs/SANITIZE_LOG.md`; `docs/DIFF_PLAN.md` records what
changed relative to the previous public revision.

## License

MIT — see `LICENSE`.
