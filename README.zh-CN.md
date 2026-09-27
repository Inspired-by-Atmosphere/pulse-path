# pulse-path · 链表式知识记忆管理

> 大型项目知识/记忆/引用体系按**链表**组织与维护——删除重接链、修改查指针、新增带出链、整理后一致性扫描。
> A linked-list discipline for managing knowledge/memory/reference systems in large projects.

- [English](README.md) · 中文版（本文件）

## 这是什么 / What this is

大型项目（多协作者 / 多文档 / 多技能 / 长期演进）的知识资产很容易被改动**打散联系**：
删掉一个文件、改一条路径，引用它的文档悄悄"断线"，下次顺着找才发现。

本技能把知识/记忆组织成**链表**：每条记忆是节点，"详见/指向/引用"是指针，
任何改动必须保持链完整——这是从数据结构借来的纪律。

核心五律 / Five laws：

1. **删除 = 重接链** — 删节点前先找出所有指向它的引用，重定向或同步删，禁止悬空指针
2. **修改 = 查前后指针** — 入链（谁引用我）+ 出链（我指向谁）
3. **新增 = 声明去向** — 每个新节点必须带出链，禁止孤岛
4. **整理后必检** — 一致性扫描（路径存在性 + 多源交叉核对）
5. **一视同仁** — 内置记忆 / 事实库 / 技能 / 文档同等适用

配套**分层激活模式**：L1 索引层（每轮可见的指针）→ L2 内容层（事实库/技能/文档/会话），
配合"索引层为空 ≠ 信息不存在"防呆，让项目记忆按需激活、不占常驻空间。

## 目录结构 / Layout

```
pulse-path/
├── README.md / README.zh-CN.md
├── scripts/check_secrets.py                 # 零依赖发布前敏感扫描
├── docs/{SANITIZE_LOG.md, DIFF_PLAN.md}
├── examples/
│   ├── MEMORY.sample.md                     # 一份可跑的指针字典样例
│   └── mem_rules.sample.json                # 规则表起点
└── skills/
    ├── pulse-path/                          # 方法论层：链表五律
    │   ├── SKILL.md
    │   ├── references/classification_rules.md   # 信息类型 → 唯一载体归属表
    │   └── scripts/scan_links.py                # 链完整性体检（路径引用 + 技能名引用）
    ├── memory-pointer-system/               # 实现层：指针怎么写、怎么体检
    │   ├── SKILL.md
    │   ├── references/{ghost-scan-v2.md, governance-engines.md}
    │   ├── scripts/{mem_guard,auditor,consolidator,curator,memory_request,
    │   │            check_l2_coverage,scan_ghost_script_refs,test_mem_guard}.py
    │   └── scripts/mem_rules.json           # 声明式规则表（引擎零业务 if）
    └── memory-governance/                   # 治理层：分层、拨层、写入流程
        ├── SKILL.md
        └── references/{consolidation-engine,curator-ticket-pipeline,
                        memory-chain-diagnosis,events-governance}.md
```

三个技能都是可直接放入 agent 的技能目录（Claude Code `.claude/skills/`、Hermes `skills/`，
或任何读取带 YAML front matter 的 `SKILL.md` 的加载器）。三者可独立使用——
多数使用者只需要 `pulse-path` + `scan_links.py`。

## 快速上手 / Quick start

1. 把 `skills/pulse-path/`（或三个技能全部）放入你的 agent 技能目录
2. 每次整理后跑体检：

```bash
python skills/pulse-path/scripts/scan_links.py <目录...>   # 退出码 0=干净，1=有悬空引用
python skills/pulse-path/scripts/scan_links.py --help      # 环境变量与选项
```

3.（可选）接上记忆体检引擎：指向你的指针文件，跑一次 dry-run

```bash
# HERMES_HOME 默认 ~/.hermes；MEMORY_FILE 默认 $HERMES_HOME/memories/MEMORY.md
python skills/memory-pointer-system/scripts/mem_guard.py --check --verbose
python skills/memory-pointer-system/scripts/test_mem_guard.py     # 自包含单元测试
```

## 配置 / Configuration

所有脚本都从环境变量解析路径，默认值跨平台且不含任何个人路径。

| 变量 | 使用方 | 默认值 | 含义 |
|---|---|---|---|
| `HERMES_HOME` | 全部 | `~/.hermes` | 记忆树根目录 |
| `SKILLS_ROOT` | `scan_links.py` | `$HERMES_HOME/skills` | 技能库根（校验 `skill://` 的基准） |
| `SCAN_DIRS` | `scan_links.py` | `$SKILLS_ROOT` + 当前目录 | 默认扫描目录（`os.pathsep` 分隔） |
| `MEM_GUARD_MEMORY` | `mem_guard.py` | `$HERMES_HOME/memories/MEMORY.md` | 待体检的指针文件 |
| `MEM_GUARD_RULES` | `mem_guard.py` | 脚本同目录 `mem_rules.json` | 声明式规则表 |
| `MEM_GUARD_SKILLS` / `MEM_GUARD_SCRIPTS` | `mem_guard.py` | `$HERMES_HOME/skills` / `scripts` | `skill://`、`script://` 校验基准 |
| `MEM_GUARD_ENV` / `MEM_GUARD_CONFIG` | `mem_guard.py` | `$HERMES_HOME/.env` / `config.yaml` | `cfg://` / `env://` 校验基准 |
| `AUDIT_MEMORY` / `AUDIT_USER` / `AUDIT_FACT_DB` | `auditor.py` | 均在 `$HERMES_HOME` 下 | 内容级审计输入 |
| `AUDIT_SKILLS` / `AUDIT_REPORT` | `auditor.py` | `$HERMES_HOME/{skills,reports}` | 技能库根 / 报告输出 |
| `CONSOLIDATE_MEMORY` / `CONSOLIDATE_USER` | `consolidator.py` | 均在 `$HERMES_HOME` 下 | 巩固检测输入 |
| `CONSOLIDATE_RULES_REF` | `consolidator.py` | `../pulse-path/references/classification_rules.md` | 分类法则表 |
| `CONSOLIDATE_REPORT` / `CURATE_REPORT` | 引擎 | `$HERMES_HOME/reports/` | 报告输出 |
| `VIKING_ROOT` | auditor / curator / `check_l2_coverage.py` | `~/.openviking/data/viking/default/…` | 语义库根（可选） |
| `OV_CLI` | `curator.py` | `$HERMES_HOME/bin/ov.exe` | 语义库 CLI（可选；缺它也能 dry-run） |
| `GOVERNANCE_MODULE_DIR` | `curator.py` | 未设置 | 可选的外部审批/决策引擎目录 |

`mem_rules.json` 里的路径支持 `~`、`$VAR`，或相对 `HERMES_HOME` 的相对路径。

可选地把这些脚本挂成定时任务（每日体检 / 夜间巩固 / 每周审计）。检测脚本**恒 `exit 0`**、
问题经 stdout 传递——原因与告警档位标定见 `skills/memory-governance/references/consolidation-engine.md`。

## 指针字典样例 / Example

```
§
mem_guard = skill://memory-pointer-system | 指针体检引擎：格式/断链/水位
rules     = doc://docs/classification.md | 信息类型→唯一载体归属表
jobs      = cron://nightly-audit | 每夜审计任务；异常经 stdout 上报
api       = cfg://SERVICE_API_KEY | 取值见 .env，勿写入记忆正文
```

`examples/MEMORY.sample.md` 是一份可直接喂给 `mem_guard.py --check` 的样例。

## 设计说明与局限 / Design notes & limitations

- **方法论优先，引擎其次**：五律才是核心资产，引擎只是一份参考实现；不依赖任何特定 agent 运行时。
- **语义库集成是可选的**：`viking://` 指针、基于 `ov` 的整理与事件治理假设存在一个 OpenViking 式
  带 CLI 的语义库。没有它时，体检/巩固/报告链路照常工作，`curator --commit` 不会写入任何地方。
- **不是数据库**：指针字典就是纯 markdown 文件，并发控制交给宿主 agent 的记忆工具，不属本仓职责。
- **水位百分比取决于你配置的 `limit`**（`mem_rules.json`）：宿主上调记忆字符上限后要同步改规则表，
  否则百分比失真（上一版就因未同步导致自带测试 1/25 失败）。
- **Windows / git-bash 提示**：脚本用 `python` 跑，路径请传原生形式（`<盘符>:/路径/script.py`）而非 MSYS 形式
  （`/c/...`）——MSYS 路径转换通常是关闭的。

## 出处 / Provenance

由长期、多协作者项目的实战沉淀而来。其中的身份、组织、路径与内部数字均已移除——
见 `docs/SANITIZE_LOG.md`；相对上一版公开修订的变化见 `docs/DIFF_PLAN.md`。

## 许可 / License

MIT — 见 `LICENSE`。
