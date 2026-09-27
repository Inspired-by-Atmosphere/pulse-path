---
name: memory-governance
description: Use when 记忆分层/治理引擎/降级拨层/写入流程/水位整理。与 pulse-path 互补。
---

# 记忆治理体系（Memory Governance）

记忆体系的分层、治理与持续自主体检生态。pulse-path 管"链表纪律"（怎么不散），本技能管"分层与治理"（谁管哪一层、什么自动、什么留人）。

## 触发场景

- 记忆分层/分类设计、决定信息放哪层
- 治理引擎（整理员 consolidator / 审计员 auditor / 体检员 mem_guard）维护与排障
- 记忆降级/拨层/写入流程设计
- 记忆水位整理（高位需降级）
- 评估本地模型是否适合当"记忆管理员 VLM"

## 核心模型

### 1. 分层三轴判据（不拍脑袋，三轴交叉定位）

| 轴 | 判据（问什么） | 决定 |
|---|---|---|
| ①知识形态（DIKW+Tulving） | Data/Info/Knowledge/Wisdom？事实/规则/做法/事件/教训？ | 落哪类结构体（语义库实体 / USER 红线 / 技能 / 事件 / 决策日志） |
| ②生命周期（档案学） | 这条多活跃？高频/偶尔/少用？ | 现行→L1 / 半现行→L2 / 非现行→归档 |
| ③作用域 | 属于用户全局/某项目/某会话？ | USER.md / 项目 AGENTS.md / 会话记录（不进记忆） |

### 2. 拨层机制（怎么向下拨）

- 触发：访问频率下降（整理员检测"长叙述+低频"）/ 价值鉴定 / 生命周期超期（retention_days）
- 动作：L1→L2（内容写 L2 结构体，L1 改指针）/ L2→L1（频繁需要时提升快取）/ 删除（留处置记录）
- 工具链：`整理员检测 → mem_guard 验证指针 → agent 按分类法则归位`

### 2b. L1→L2 降级执行清单

水位标红后做降级整理时按此执行，别直接动手：

1. **先验 L2 覆盖，再降 L1**（防断片关键）：读 L2 载体，逐条确认其内容**已包含 L1 条目的全部事实点**。⚠️ 陷阱：L1 可能比 L2 **新**——动态事实（如硬件状态、项目口径）后来才发现，L2 实体还是旧版。L1 比 L2 新时必须**先同步 L2**（写实体 + 重索引，`vector_status: complete` 验证，检索命中）再降 L1。
2. **降级必须走记忆工具，禁直接 write_file/patch 改 MEMORY.md**：记忆工具有防 drift 保护——文件被外部编辑过会拒绝写入（报 `content that wouldn't round-trip`，存 `.bak` 快照后拒写）。用批量原子提交（单次完成全部替换，按最终预算检查）。
3. **replace 语义**（核验源码后定）：`old_text` 是 substring 匹配用来定位整条，命中后**替换整条**；old_text 命中多条不同条目会报错（用条目前缀确保唯一）；批量中间溢出无所谓，只查最终态。
4. **先算节省再交方案**：脚本按条算 `len(old)-len(new)` 合计，给用户精确的"当前→降级后水位% + 余量"。候选表逐条列 L2 载体路径（`skill://` / `viking://` / `doc://`），用户批准后才执行（用户红线：降级不删条目、指针类冗余线索是防御）。
5. **验证闭环**：工具返回 usage 确认新水位 → 读文件抽查替换落盘 → 检索命中同步后的新事实。⚠️ 批量调用返回里的 `replaced_entries`/`removed_entries` 是**替换前快照**（旧内容）——看到"返回的还是旧文本"别误判为没写进去；确认生效看 usage 字符数下降 + grep 文件确认新内容已出现。

### 3. 记忆脉络（怎么成脉路）

```
L1 路由层（指针）──出链──> L2 结构体层（语义库实体/技能/文档，互相引用）
         ↑                        ↑
  分类法则表（路由规则：哪类走哪条链）
```

- 出链 L1→L2、入链反向索引（谁指向我）、交叉参照分面标签
- 死点防呆：**索引层为空 ≠ 信息不存在**

### 4. 写入流程角色分离

- **会话中的记忆发起者不负责写入记忆**，交付给"记忆管理员"做分类/去重/归位后入库
- 流水线：会话 agent 只产（`memory_request.py` 落 ticket 队列）→ curator（唯一写口）→ 质量门三道 → 结构体入库 → 决策登记簿记档
- 参考业界 write pipeline：Anthropic memory / mem0 `add()` / MemGPT / OpenAI `save_memory_note`；同构背书 = 专职 Memory Curator Agent / MemoryHub（worker 只发 event，专职 curator 独有写权）

## 已落地产物

| 产物 | 路径 | 说明 |
|---|---|---|
| 分类法则表 | `pulse-path/references/classification_rules.md` | 8 法则 + 载体归属表（SSOT），体检员/整理员读取 |
| 体检员 | `memory-pointer-system/scripts/mem_guard.py` | 指针格式/存在性/水位，低级自动修 |
| 整理员 | `memory-pointer-system/scripts/consolidator.py` | 睡眠期巩固检测：水位/降级候选/重复/过期/类目，只读+报告落盘 |
| 审计员 | `memory-pointer-system/scripts/auditor.py` | 内容级审计：分类法则遵守/跨载体重复/指针断链/ticket→入库链路核对 |
| 记忆管理员 | `memory-pointer-system/scripts/curator.py` | 唯一写口：消化 ticket 队列 → 质量门三道 → 写入结构体 → 决策登记簿记档；高危动作=备份+自动写档；兼治自动抽取产物（`--govern-events`） |
| 发起端 | `memory-pointer-system/scripts/memory_request.py` | 会话 agent 只产不写：落 ticket 队列（jsonl） |
| 声明式规则表 | `memory-pointer-system/scripts/mem_rules.json` | watermark.files + consolidation 阈值 + curation 段（ticket 路径/质量门阈值），引擎零业务 if |

调度：整理员每日睡眠期 / 审计员每周 / 记忆管理员每日 / 体检员每日，`deliver=local` 不打扰（报告落 `$HERMES_HOME/reports/`）。

## 记忆链健康体检基准

诊断"记忆链是否正常"时对照此表——**不要把修复前的状态当预期**（曾出现整理员长期 exit 127 零写入、体检员被误标 failed、去重门误杀真实偏好）。

| 环节 | 健康基线 | 变坏的信号 |
|---|---|---|
| 记忆管理员调度 | script 指 `.py`（**禁 `.sh`**），`last_status=ok` | 变回 `.sh` / exit 127 / script failed |
| 写入 | `--commit` 后结构体出现新文件且检索命中 | 只出 duplicate/discard、零写入 = 门太宽或链路断 |
| 去重门 | 同类别 + 重叠≥阈值 + 跳过交接包/overview/profile + 剔除纯数字 token | 配置被改回"全库重叠≥2"（会误杀真实偏好） |
| 高危动作 | `approval_policy: auto_with_backup`（备份→自动写→记档），登记簿有记档 | 出现 `pending_approval` 孤儿 |
| 体检员 | 恒 exit 0；`last_status=error` = 真异常 | 脚本又改回 exit 1 |
| 自动抽取 | `ov observer queue` 0 pending + 每日事件文件数正常 | SessionCommit processed 不增长、events 日期停更 |
| 决策登记簿 | 近日记档连续 | 每日巡检断更 |

⚠️ **查自动抽取必须取当前实时值**：`processed` 是**累计计数**（修复后持续上涨才健康）。判断"在不在转"看"**是否仍在增长 + 每日是否有新事件文件**"，**禁引用历史会话/记忆里的旧计数当现状**（曾拿修复前的旧数字误判自动抽取已断，实际健康）。

**验收口径**：curator 端到端通过 = **结构体实际写入且内容正确**，不是"不重复写/队列清空"。

## 坑位

- **降级 ≠ 压缩**：长叙述条目的正确降级 = 内容写 L2 结构体 → L1 只留指针。压缩后仍留 L1 是错的——具体事项不该跑一级，应顺着指针找结构体。治理角色应标"降级到 L2 只留指针"而非"压短指针"
- **去重门误杀**：原"全库关键词重叠≥2"太宽，真实偏好被误杀（证据还指错到交接包文件）。改：**同类别目录 + 重叠≥较高阈值 + 跳过交接包/overview/profile 文件 + 剔除纯数字 token（日期防假重叠）**。参数全声明式（`curation.quality_gate`）
- **高危审批门改备份+自动**：请批通知不可靠会搁置 → 高危动作（删除/重构/覆盖类）不挂审批门，改为「自行备份已有目标 → 自动写档 → 决策登记簿记档」，用户随时可让汇报（`approval_policy: auto_with_backup`）
- **检测脚本退出码恒 0**：问题经 stdout 传递。旧"red 级 exit 1=有预警"设计已废弃（会被调度器误标 script failed 掩盖预警）
- **调度包装脚本必须用 `.py`，禁 `.sh`**：Windows 下从建号起每次 exit 127（调度器把带 `\` 的 Windows 路径传给 bash，反斜杠被当转义吞掉：`C:UsersLenovo…`）。同目录 `.py` 全部正常。上线新调度脚本默认 `.py`
- **结构体 URI 坑**：路径必须带 `default` 层级（`viking://user/default/memories/...`）；新建文件要 `--mode create`（默认 replace 对不存在 URI 不建）；改 embedding 模型（维度不变）也需配置 `allow_metadata_override`，否则拒启要求重建索引
- **治理执行原则**：降级/指针化/收编/整脉整理这类"该治理机构负责的活"，**必须由治理链自动执行，不能等用户逐次下令**——"只读+报告"和"L1 指针 report_only"两个设计叠起来，会把"动手"永远留在人工。正解：整理员从"只报"升级为"产出工作单并执行"，安全门 = ①`check_l2_coverage.py` 验证 L2 覆盖 ②执行前备份记忆文件（带时间戳，可回滚）③体检员事后验证 0 断链 ④决策登记簿记档；用户只看报告、不满意可回滚。`report_only` 的语义是"不自动凭空写 L1"，不是"降级执行也要人逐条批准"
- **空转 ≠ 引擎坏**：curator 只消费 ticket 队列，**不治理自动抽取产物**——若 ticket 队列无新条（会话 agent 普遍不用 `memory_request.py`）+ 自动抽取每天照常写入（绕过质量门）= 引擎健康、治理层输入断。诊断"部门没干活"先看**ticket 队列最后时间戳** vs **结构体最近写入**，别把空转当整条记忆链死了。业界定位：自动抽取（Background）才是主路径，agent 主动提交（Hot Path）只是补充；curator 应对自动抽取候选做准入/去重/归位，而不是只等 ticket
- **长文档/事件去重必须用「绝对重叠 + 相似度比例」双条件**：ticket 去重的"重叠≥4"照搬到长事件文件必全误报——抽取产物几百字，模板结构词（Summary/ChatLog/memory/type/source/id/name）每篇都有，任意两篇天然重叠 20+ 词；光加停用词是打地鼠（滤一批又浮一批环境词：路径/AI/Users…）。正解 = `overlap ≥ dedup_min_overlap 且 overlap/min(双方词数) ≥ dedup_min_similarity`（默认 0.3）。实测 0.52/0.54 的真重复与 0.05/0.03 的跨主题误报被干净分开
- **自动生成的目录概述（`.overview.md`）要进治理 skip**：每目录一个，内容与同主题事件天然重叠，不跳过每天必报。skip 清单同时作用于**治理对象收集**和**去重对比对象**——两处都要过滤，只过滤一处会漏
- **例行性重复事件豁免**：每天写高度模板化事件的定时任务，其产物经确认后加入 `dedup_skip_files` 豁免，避免每天误报噪音；历史记录一字不动（日志类铁律）
- **类目错位文件用 `ov mv` 移动，禁直接动文件系统**：`ov mv <src-uri> <dst-uri>` 会同步向量索引；直接改 `~/.openviking/data/...` 文件则检索仍取旧路径数据。移完用 `ov ls` 验证源消失/目标命中
- **治理链只管结构不管语义正确性**：体检/整理/审计全部校验"指针格式/断链/水位/重复/分类"，**没有一环校验"内容是否过期、数字是否仍是权威口径"**——口径换版后旧值残留在结构体/AGENTS.md/技能里无人抓。正解 = **SSOT 口径对账**：规则表声明式登记关键口径（`{词条, 期望值, SSOT 位置, 禁用语}`），整理员每日扫全载体比对——命中禁用语 🔴 自动修（备份+记档）、数字不符 🟡 待核、状态词（待批/进行中/未 commit）超期 🟡 复核。**改口径时必须同步清旧值**（新源落盘 ≠ 旧值消失），对账扫描才能从源头抓残留

## 验证方式

- mem_guard 0 断链（正常指针 / 待全量版）
- 本地模型管理员能力测试（达标才当整理员 VLM）：载体判定 / 去重判断 / identity-role-preferences 三层 / 生命周期 L1-L2-销毁 / 结构化抽取 5 项，弱模型能过即够
- 降级后信息找回：注入降级指针问内容，模型应能解析 target 答全

## 支持文件

- `references/consolidation-engine.md`：整理员/审计员引擎设计细节 + 退出码语义 + 声明式规则表模式
- `references/curator-ticket-pipeline.md`：记忆管理员写入治理管线（ticket 队列/质量门三道/高危动作策略/auditor 链路核对/业界调研要点）
- `references/memory-chain-diagnosis.md`：**记忆链健康体检 / "变笨·习惯消失"排查法**（五查法 + 根因实录）
- `references/events-governance.md`：**自动抽取产物治理**（`curator --govern-events` 三查逻辑/参数表/去重双条件调优/例行事件豁免/归位）
