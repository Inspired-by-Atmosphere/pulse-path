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
├── CHANGELOG.md · LICENSE · requirements.txt
├── .github/workflows/ci.yml                 # 4 组合矩阵：ubuntu/windows × Python 3.10/3.13
├── scripts/
│   ├── check_secrets.py                     # 零依赖发布前敏感扫描
│   └── run_checks.py                        # 统一自检入口（离线跑全部检查）
├── mcp_server/
│   ├── pulse_path_mcp.py                    # stdio JSON-RPC MCP server（纯标准库）
│   ├── test_mcp.py                          # 62 条协议断言（离线）
│   └── README.md
├── docs/SANITIZE_LOG.md · docs/RESULTS.md
├── examples/
│   ├── MEMORY.sample.md                     # 一份可跑的指针字典样例
│   ├── mem_rules.sample.json                # 规则表起点
│   ├── facts_ssot.sample.json               # 填好的口径对账规则示例
│   └── quickstart.sh / quickstart.bat       # 60 秒演示，任意 cwd 可跑
└── skills/
    ├── pulse-path/                          # 方法论层：链表五律
    │   ├── SKILL.md
    │   ├── references/classification_rules.md   # 信息类型 → 唯一载体归属表
    │   └── scripts/{scan_links,scan_link_graph}.py
    ├── memory-pointer-system/               # 实现层：指针怎么写、怎么体检
    │   ├── SKILL.md
    │   ├── references/{ghost-scan-v2.md, governance-engines.md}
    │   ├── scripts/{mem_guard,auditor,consolidator,curator,memory_request,
    │   │            check_l2_coverage,scan_ghost_script_refs,test_mem_guard,
    │   │            scan_archived_deps,stress_pointer_test,project_registry}.py
    │   └── scripts/mem_rules.json           # 声明式规则表（引擎零业务 if）
    └── memory-governance/                   # 治理层：分层、拨层、写入流程
        ├── SKILL.md
        ├── references/{consolidation-engine,curator-ticket-pipeline,
        │               memory-chain-diagnosis,events-governance}.md
        └── scripts/ssot_check.py            # 口径对账（SSOT 漂移）
```

三个技能都是可直接放入 agent 的技能目录（Claude Code `.claude/skills/`、Hermes `skills/`，
或任何读取带 YAML front matter 的 `SKILL.md` 的加载器）。三者可独立使用——
多数使用者只需要 `pulse-path` + `scan_links.py`。

## 能力一览 / Engines at a glance

全部引擎都是标准库 Python；能声明成阈值的都放在 `mem_rules.json`，不加 `--json` 时输出给人读。

| 引擎 | 回答什么问题 | 退出码 |
|---|---|---|
| `scan_links.py` | 这些文档里的路径/技能引用还都能解析吗？ | 0 干净 / 1 有悬空 |
| `scan_link_graph.py` | 文档图谱长什么样——幽灵链接、孤立节点、反向索引、枢纽在哪？ | 恒 0 |
| `mem_guard.py` | 每条指针格式对吗、目标存在吗、摘要是否超长、文件水位是否过高？ | 恒 0 |
| `check_l2_coverage.py` | 缩短摘要之前：要丢掉的每个 token 是否已在 L2 落盘？ | 恒 0 |
| `auditor.py` | 内容级审计：分类法则遵守、跨载体重复、ticket→入库链路。 | 恒 0 |
| `consolidator.py` | 睡眠期检测：水位、降级候选、重复、过期、类目错位。 | 恒 0 |
| `ssot_check.py` | 已作废的数字/说法是否还残留在活载体里（口径漂移）？ | 恒 0 |
| `curator.py` | 唯一写口：消化 ticket 队列，过质量门后写入结构体。 | 恒 0 |
| `memory_request.py` | 会话侧：只产 ticket，不直接写记忆。 | 恒 0 |
| `scan_ghost_script_refs.py` | 文档里提到的脚本，真实存在吗？ | 恒 0 |
| `scan_archived_deps.py` | 哪些已归档技能仍被活文件引用？ | 恒 0 |
| `stress_pointer_test.py` | 上下文塞满时，弱模型还能正确解析指针吗？ | 0 通过 / 1 判分失败 / 2 环境错误 |
| `project_registry.py` | 注册项目 + 它的那条头指针（默认 dry-run）。 | 0 / 1 |
| `mcp_server/pulse_path_mcp.py` | 把上面这些检查暴露为 MCP tools（stdio）。 | JSON-RPC |

**一次跑完全部检查（离线）**：`python scripts/run_checks.py` —— CI 跑的就是它。

## 快速上手（60 秒）/ Quick start

只要 Python 3 标准库——无需安装任何依赖。把体检引擎指向一份**一次性沙箱**里的样例，直接看它抓断链：

```bash
SANDBOX=/tmp/pp-demo                       # 任意一次性目录
mkdir -p "$SANDBOX/memories"
cp examples/MEMORY.sample.md "$SANDBOX/memories/MEMORY.md"
printf '# User profile\n- Demo line\n' > "$SANDBOX/memories/USER.md"

HERMES_HOME="$SANDBOX" python skills/memory-pointer-system/scripts/mem_guard.py --check --verbose
SKILLS_ROOT=skills     python skills/pulse-path/scripts/scan_links.py skills
```

`--check` 是 dry-run：只报告不写回，绝不会改动你的指针文件。上面两条命令的真实输出如下（`$SANDBOX` 代表你用的那个目录）：

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

`L14`/`L15` 报错是预期的：沙箱里没有 `skills/` 目录——这正是这套体检要抓的那类错误；`L17`/`L18` 则是样例夹具**故意**留的两条悬空指针。

捷径：`bash examples/quickstart.sh`（Windows 用 `examples\quickstart.bat`）在**任意工作目录**下
做同样的演示，然后跑一遍完整自检。只要检查不要演示就跑 `python scripts/run_checks.py`
（单元测试 + 敏感扫描 + 链完整性 + 各引擎 `--selftest` + MCP 协议测试）；CI 跑的就是这条命令。

接着把它接进你自己的项目：

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
| `MEM_GUARD_CRON` | `mem_guard.py` | `$HERMES_HOME/cron/jobs.json` | 校验 `cron://` 指针的任务库（文件不存在⇒`?`，不误报） |
| `SSOT_RULES` | `ssot_check.py` | `memory-pointer-system/scripts/mem_rules.json` | 含 `facts_ssot` 段的规则表 |
| `PROJECT_RULES` / `PROJECT_MEMORY` / `PROJECT_SKILLS` | `project_registry.py` | 脚本同目录规则表 / `$HERMES_HOME/memories/MEMORY.md` / `$HERMES_HOME/skills` | 注册落点 |
| `ARCH_SCAN_ROOTS` | `scan_archived_deps.py` | `$HERMES_HOME` + 当前目录 | 默认扫描根（`os.pathsep` 分隔） |
| `PP_STRESS_BASE_URL` / `PP_STRESS_API_KEY` / `PP_STRESS_MODEL` | `stress_pointer_test.py` | 未设置 | 弱模型压测用的 OpenAI 兼容端点/密钥/模型名 |
| `PULSE_PATH_MCP_TIMEOUT` | `mcp_server/pulse_path_mcp.py` | `120`（秒） | 单次被包装引擎调用超时 |
| `DOCS_HOME` | `auditor.py` | `~/Documents` | 相对文档引用的解析根 |

`mem_rules.json` 里的路径支持 `~`、`$VAR`，或相对 `HERMES_HOME` 的相对路径。

可选地把这些脚本挂成定时任务（每日体检 / 夜间巩固 / 每周审计）。检测脚本**恒 `exit 0`**、
问题经 stdout 传递——原因与告警档位标定见 `skills/memory-governance/references/consolidation-engine.md`。

## 实测结果 / Results (measured)

Windows 11、Python 3.14.9、单台笔记本；用 `python scripts/run_checks.py` 可完整复现：

| 指标 | 值 |
|---|---|
| `run_checks.py` 里的离线检查 | **9/9 通过** |
| 各类断言合计 | 47（单测）+ 20 + 27 + 37 + 26 + 34（引擎 selftest）+ 62（MCP 协议） |
| 本仓自身敏感扫描 | `CLEAN` |
| 本仓自身链完整性 | `0` 条悬空引用 |
| `mem_guard.py --check`（5 条指针夹具） | ≈0.37 s |
| `scan_link_graph.py`（整仓） | ≈0.59 s |
| `scan_links.py`（仓库 skills） | ≈0.60 s |
| MCP `initialize` + 一次 `tools/call` | ≈0.65 s 往返 |

墙钟时间随磁盘缓存与文件数浮动，该被当作验收标准的是断言数与 CLEAN/悬空结果。
方法与局限见 `docs/RESULTS.md`。

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
- **这里不做检索**：没有 embedding、没有索引、没有向量库；指针由宿主 agent 自己解析（读摘要行、打开 target）。
  本仓校验的是**链图**，不是检索——它补记忆库，不替记忆库。
- **弱模型压测需要模型**：`stress_pointer_test.py` 走 OpenAI 兼容的 `/chat/completions`；没配端点时只能
  `--plan` 打印将发送的内容。它证明的是“弱模型**也能**解析指针”，不是“你的模型一定会”。
- **MCP server 是薄包装**：它解析引擎的人读报告；报告格式变了就降级（保留原文 + 附警告），不编结论。
  所有 tool 只读、只调 `--check`。

## 出处 / Provenance

由长期、多协作者项目的实战沉淀而来。其中的身份、组织、路径与内部数字均已移除——
见 `docs/SANITIZE_LOG.md`；本版实测数字见 `docs/RESULTS.md`。

## 许可 / License

MIT — 见 `LICENSE`。
