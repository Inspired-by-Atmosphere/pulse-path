# DIFF_PLAN — 本 staging 超集相对现有公开仓的改造计划

**基准**：现有公开仓工作副本（15 个文件：`README.md`、`.gitattributes`、
`skills/pulse-path/{SKILL.md,references/classification_rules.md,scripts/scan_links.py}`、
`skills/memory-pointer-system/{SKILL.md,scripts/*9 文件}`）。
**目标**：`E:\Hermes-win\opensource\pulse-path\`（staging 超集，23+ 文件）。
**原则**：只增不删知识资产；删的是**受限信息**（隐私/保密/内部口径），保留的是**方法**。

---

## 0 审计结论（现存 15 文件逐条 G2/G3/G4 违规）

| # | 文件 | 违规点（类型） | 处置档 |
|---|---|---|---|
| 1 | `README.md` L12 | 项目/赛事名（G3） | 删 |
| 2 | `README.md` L56-57 | 赛事名 + 项目代号 + 内部数字（`98%→56%`）+ 成果描述（G3） | 改写为匿名量级 |
| 3 | `README.md` L61 | 署名与规范不一致（`Inspired-atm`，非 G2/G3 但属元数据违规） | 改为 `Inspired-by-Atmosphere` |
| 4 | `README.md` 全文 | 无英文主 README、无配置表、无局限、无 LICENSE 引用（G4 可用性） | 重写 + 新增 zh-CN |
| 5 | `skills/pulse-path/SKILL.md` L8/L64-69 | 项目名 + 内部数字（`98%→56%`、`24→16`、`26 条`、`96.2% 假数字`、`T7 采购`、`学案机`）（G3） | 整节改匿名化 |
| 6 | `skills/pulse-path/SKILL.md` L71-81 | 内部整理模板含 SSOT 口径数字（`69.42→63%±6`、`215Hz→249Hz`、`8.6K`）+ 内部目录名（员工工作区/微云/学案）+ 真实密钥提示（G3/G2） | 保留 8 步方法，删全部数字与内部目录名 |
| 7 | `skills/pulse-path/SKILL.md` L24 | 本机绝对路径示例（`C:\...\sleep-edfx`）（G4） | 改为 `<DATA_DIR>/dataset` |
| 8 | `skills/pulse-path/SKILL.md` L93 | 本机实测字符数（G3 弱） | 改为定性描述 |
| 9 | `skills/pulse-path/scripts/scan_links.py` L31-34 | 两处本机绝对路径常量（G2/G4） | 改环境变量 + 可移植默认 |
| 10 | `skills/memory-pointer-system/SKILL.md` L42/L58/L64/L68/L117 | 5 处本机绝对路径（G2/G4） | 改 `$HERMES_HOME` / 仓库内相对路径 |
| 11 | `skills/memory-pointer-system/SKILL.md` L60-75 | 私有 Gitee 库地址 + 账号路径 + 本机三副本路径（G2/G3） | 改为通用三副本描述 |
| 12 | `skills/memory-pointer-system/SKILL.md` L73/L90/L92/L123/L161-164/L177 | 调度任务 ID ×6（本机实例标识）（G3 弱） | 删除或改描述性说法 |
| 13 | `skills/memory-pointer-system/SKILL.md` L84-146 | 内部项目名/竞赛名/内部文档名/内部硬件档案/本机 GPU 型号/内部数字（`99%→61%`、`99%→85%`、`30→34`）（G3） | 保留判据与坑位，删全部标识与数字 |
| 14 | `skills/memory-pointer-system/SKILL.md` L156/L164 | 私有技术库路径 + 内部库名 + 另一私有仓的同步脚本（G2/G3） | 改为通用"开发区/验证区/归档区" |
| 15 | `skills/memory-pointer-system/SKILL.md` L172-191 | 内部维护纪律段（版本保护/同步入库纪律），部分是内部流程（G3） | 压缩为通用"核心脚本纳入多副本同步" |
| 16 | `skills/memory-pointer-system/SKILL.md` L206-224 | 本机状态库路径、fact 库路径、内部插件名、容器主机名（G2/G4） | 路径改 `$HERMES_HOME/...`，删内部插件/主机名 |
| 17 | `skills/memory-pointer-system/SKILL.md` L227-231 | 引用了仓库内**不存在**的 `references/ghost-scan-v2.md`、`references/maintenance.md`（悬空指针，讽刺性违规） | 补入 `ghost-scan-v2.md`；`maintenance.md` 引用删除 |
| 18 | `skills/memory-pointer-system/scripts/{mem_guard,auditor,consolidator,curator,check_l2_coverage}.py` | 头部常量共 17 处本机绝对路径（G2/G4） | 改环境变量 + 可移植默认 |
| 19 | `.../auditor.py` L146 | 硬编码家目录前缀拼接（G4） | 改 `DOCS_HOME`/`HERMES_HOME` |
| 20 | `.../consolidator.py` L157 | 引擎内硬编码项目名（G3，且违反"引擎零业务 if"自定原则） | 改规则表 `consolidation.project_prefixes` |
| 21 | `.../curator.py` L25 | 硬编码另一技能的私有目录作为可选依赖（G4） | 改 `GOVERNANCE_MODULE_DIR`，默认不加载 |
| 22 | `.../curator.py` L394 | 私有通信渠道名（G3） | 改"通知渠道" |
| 23 | `.../mem_rules.json` | 11 处本机绝对路径 + 内部目录名 + 用户名 token（G2/G3/G4） | 路径改 `${HERMES_HOME}`；扫描块置空 + `enabled:false`；删用户名 token |
| 24 | `.../scan_ghost_script_refs.py` | 顶部即以常量写死三个本机目录，且**导入即执行**（无 `main()`，无法 `--help`）（G4） | 重写为 `main()` + 全 CLI/规则表配置 |
| 25 | `.../test_mem_guard.py` L53/L65 | 断言依赖本机真实路径与真实技能（G4：陌生机器必挂） | 重写为自包含沙箱 |
| 26 | `.../test_mem_guard.py` L103-108 | 水位断言与规则表 `limit` 口径不一致 → 在你本机实测 **24/25（1 失败）**，而文档宣称 25/25（G4 可用性 + 结论失真） | 断言改显式 `files`+`limit`，现 26/26 |
| 27 | 全仓 | 无 `LICENSE`/`.gitignore`/`requirements.txt`/`docs/SANITIZE_LOG.md`/`CHANGELOG.md`/`scripts/check_secrets.py`/`examples/`（规范 §3 结构缺失） | 新增 |
| 28 | 全仓 | 提交了 `__pycache__/mem_guard.cpython-311.pyc`（二进制产物入库） | 删除 + `.gitignore` |

**未发现**（S1 零命中）：真实姓名（审计中共 4 处，均为"成员"泛指而非真名）、凭据/私钥/令牌、
邮箱、内网 IP、MAC、主机名、学号。**唯一出现的"姓名型"信息是用户名出现在路径里**，已随路径一并处理。

---

## 1 新增文件

| 文件 | 理由 |
|---|---|
| `README.zh-CN.md` | 规范 §3 要求中英双 README；原仓只有中文单版且含 G3 |
| `LICENSE` | 规范 §3；MIT，署名统一 `Inspired-by-Atmosphere` |
| `CHANGELOG.md` | 规范 §3；记录本次脱敏与超集改造 |
| `.gitignore` | 规范 §3；防 `__pycache__`/`.env`/报告产物/备份文件再次入库 |
| `requirements.txt` | 规范 §3；显式声明"仅标准库"，并列出可选集成 |
| `docs/SANITIZE_LOG.md` | 规范 §2 强制要求（类别/条数/动作，不记原值） |
| `docs/DIFF_PLAN.md` | 本文件（交付物 d） |
| `scripts/check_secrets.py` | 规范 §3 + 门禁 S1 要求；零依赖 |
| `examples/MEMORY.sample.md` | 让使用者三步内跑通体检（G4）；含 1 条故意悬空指针以演示告警 |
| `examples/mem_rules.sample.json` | 规则表起点模板，让"声明式扩展"可照抄 |
| `skills/memory-governance/SKILL.md` | **增量技能**（Hermes 侧有、公开仓无）：分层三轴/拨层机制/写入角色分离/治理链体检基准 |
| `skills/memory-governance/references/consolidation-engine.md` | 增量：三引擎分工 + 声明式规则表 + 调度退出码语义 + 本地模型管理员评测 5 项 |
| `skills/memory-governance/references/curator-ticket-pipeline.md` | 增量：ticket 写入管线 + 质量门三道 + 高危动作策略 + 业界四形态调研 |
| `skills/memory-governance/references/memory-chain-diagnosis.md` | 增量："变笨·习惯消失"五查法 + 一次完整根因链实录（已匿名化） |
| `skills/memory-governance/references/events-governance.md` | 增量：自动抽取产物治理 + 去重双条件调优 + 例行事件豁免 |
| `skills/memory-pointer-system/references/ghost-scan-v2.md` | **补断链**：原 `SKILL.md` 引用了它但仓内不存在 |
| `skills/memory-pointer-system/references/governance-engines.md` | 增量：六角色全景 + 权限三层 + 事实库退役判据 + embedding 坑 |

## 2 修改文件

| 文件 | 修改摘要 |
|---|---|
| `README.md` | 重写为**英文主版**：定位一句话 / 为什么 / 目录结构 / ≤3 步 Quickstart / 全环境变量表 / 局限 / License；删 3 处 G3，署名修正 |
| `skills/pulse-path/SKILL.md` | 保留全部方法论（五律/分层激活/死点测试/大整理 8 步/坑位/借鉴参考）；案例改匿名化、数字删或量级化；路径占位化 |
| `skills/pulse-path/references/classification_rules.md` | 内容合规，**未改正文**（仅随行尾 LF 归一） |
| `skills/pulse-path/scripts/scan_links.py` | 常量改 `HERMES_HOME`/`SKILLS_ROOT`/`SCAN_DIRS`；新增 `--help`；默认扫描目录改为"技能库 + 当前目录" |
| `skills/memory-pointer-system/SKILL.md` | 保留全部实现层知识（指针语言/三级条目/体检分级/L2 覆盖校验/整脉收编/渐进披露/坑位）；删 5 处本机路径、6 个调度 ID、私有库地址、内部项目与数字；维护纪律压缩；支持文件清单更新并消除断链 |
| `.../scripts/mem_guard.py` | 7 个路径常量→环境变量；新增 `_p()/resolve_path()`；规则表路径支持 `~`/`$VAR`/相对；新增 `--help`；指针文件缺失时给提示而非 traceback；报告目录自动创建 |
| `.../scripts/auditor.py` | 6 个路径常量→环境变量；家目录硬编码拼接→`DOCS_HOME`/`HERMES_HOME`；`os.listdir` 加存在性保护；报告目录自动创建；新增 `--help` |
| `.../scripts/consolidator.py` | 4 个路径常量→环境变量；规则表路径解析；项目前缀改声明式 `project_prefixes`；报告目录自动创建；新增 `--help` |
| `.../scripts/curator.py` | 4 个路径常量→环境变量；`GOV_DIR` 改 `GOVERNANCE_MODULE_DIR`（默认不加载）；ticket 目录支持 `~`/`$VAR`；报告目录自动创建；渠道名通用化 |
| `.../scripts/memory_request.py` | 增加 `HERMES_HOME`/`resolve_path`；ticket 目录解析同上；文档示例去掉项目名 |
| `.../scripts/check_l2_coverage.py` | `HERMES_HOME`/`VIKING_ROOT` 默认值跨平台化；`MEM` 由导入期常量改为 `main()` 内 argparse 位置参数；新增 `--help`；文件缺失给提示；补 `argparse`（+`import`） |
| `.../scripts/scan_ghost_script_refs.py` | **重写**：导入即执行 → `main()`；三区目录、搜索根、skip 全部 CLI/规则表可配；新增 `--help` |
| `.../scripts/test_mem_guard.py` | **重写为自包含**（tempfile 沙箱 fixture）；修正水位断言口径；新增"相对路径按 HERMES_HOME 解析"断言；修复原 1/25 失败 → 26/26 |
| `.../scripts/mem_rules.json` | 绝对路径→`${HERMES_HOME}` 表达式；幽灵扫描块置空 + `enabled:false` + 配置说明；`consolidation.project_prefixes` 新增；删用户名 token；注释通用化 |
| `.gitattributes` | 扩展到 `.json/.yml/.yaml/.sh` 的 LF 归一（根治 CRLF 假 diff） |

## 3 删除 / 改写段落（受限信息，逐条）

| 段落（原位置） | 删除/改写 | 理由 |
|---|---|---|
| `README.md` 实战案例整节 | 改写为"由长期多成员项目实战沉淀" | 含赛事名/项目代号/内部数字（G3） |
| `pulse-path/SKILL.md` "实战实例（国创项目）" | 改写为"实战实例（匿名化）" | 项目名 + 内部数字 + 具体数据集名（G3） |
| `pulse-path/SKILL.md` 大整理模板 5/7/8 条 | 删具体口径数字与内部目录名，保留"抽一张替换表、全体共用" | 内部口径与数字（G3） |
| `pulse-path/SKILL.md` "大项目启动=百分百启用（2026-08-11 用户要求）" | 改为"默认启用" | 去内部决策记录口吻 |
| `memory-pointer-system/SKILL.md` 三副本同步渠道段 | 改写为通用"权威库/clone/使用副本"+ 同步脚本职责 + 同步坑 | 私有库地址 + 本机路径 + 调度 ID（G2/G3） |
| 同上 "实测价值" 段中的凭据键名 | 改为 `cfg://X` 通用示例 | 泄露本机 `.env` 键名（G2） |
| 同上 "大规模整脉收编/脉络规划" 段 | 删项目名、内部文档名、内部实体名 | G3 |
| 同上 "v2 体系版本保护与同步纳入" 段 | 压缩进坑位"核心脚本纳入多副本同步" | 内部维护纪律（G3） |
| 同上 "内置记忆盘点汇报法" 段 | 删内部弹窗/确认门细节，保留"先汇报后动手"纪律 | 内部工具行为细节 |
| 同上 "记忆分层审计法" 中内部插件名/容器主机名 | 改为"某插件/容器环境的主机名" | 内部标识（G3） |
| `memory-pointer-system/SKILL.md` 对 `references/maintenance.md` 的引用 | 删除 | 该文件不存在（悬空指针） |
| `curator.py` 中 context 治理引擎私有路径 | 删除硬编码，改环境变量 | 指向另一私有技能目录（G2/G4） |
| `mem_rules.json` 幽灵扫描的三个私有目录 | 置空 + `enabled:false` | 私有库/本机路径（G2/G4） |

**明确保留、不删**：中文正文（产品的一部分）；第三方公开项目引用（claude-mem / Zettelkasten /
broken-link-checker / Zettelkasten / OpenViking 等）；技术日期；所有方法论、判据、坑位、清单。

---

## 4 staging 仓库内**未纳入**的候选（供用户决策）

| 候选 | 现状 | 未纳入理由 |
|---|---|---|
| `jieyuan-system` 技能（阶元系统，含 7 references + 4 脚本） | 只在 Hermes 侧 | 内容含本机系统自省记录、内部角色设计、运行调查实录，**并非记忆链通用方法**；脱敏成本高、通用价值低。若要做，需单独立项重写 |
| `governance-approval` 插件（`plugin.yaml` + `__init__.py`，author 标为自定义治理） | 只在 Hermes 插件目录 | 与记忆链只有"可选审批接缝"关系；本仓已用 `GOVERNANCE_MODULE_DIR` 留出通用接缝，不需要把该插件随仓发布 |
| `memory-pointer-system/SKILL.md` 中"记忆分层审计法"整节 | 已保留并通用化 | 若用户认为过于偏工具细节，可再砍 |

## 5 门禁结果（S1–S5）

见 `CHANGELOG.md` 末尾指引；原始命令输出随交付回执提供（本文件不重复粘贴，以防两处结论不一致）。

- S1 `python scripts/check_secrets.py .` → 见回执（原始输出）
- S2 路径泄漏 grep → 见回执
- S3 空跑：`test_mem_guard.py` + 8 个脚本 `--help` → 见回执
- S4 结构核对：`git ls-files | wc -l` / `du -sh` → 见回执
- S5 敏感字反查 → 见回执（含 `scripts/check_secrets.py` 自指白名单说明）

## 6 交付边界

- 只建本地提交，**未 push、未建远程**。
- `C:\Users\Lenovo\Documents\pulse-path`（三副本同步的 clone 端）**一字未改**；
  Hermes 技能目录亦未改动。
- 本仓是**超集**：现有公开仓的每个知识资产都能在新仓找到对应物（见 §2/§3 逐条映射）。
