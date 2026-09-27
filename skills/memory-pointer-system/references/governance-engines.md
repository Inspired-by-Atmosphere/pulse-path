# 治理引擎（整理员 / 审计员）与记忆治理生态

mem_guard 管"体检"（指针/水位），治理引擎管"内容级整理与审计"。这是"持续自主体检生态"蓝图的实现层。

## 六角色管理员全景

| 角色 | 引擎 | 触发 | 主责 |
|---|---|---|---|
| ①抽取员 | 会话自动抽取 | 会话末自动 | 对话 → 结构化事实 |
| ②体检员 | `mem_guard.py` | 每日一次 | 指针断链/格式/水位 |
| ③整理员 | `consolidator.py` | 每日睡眠期 | 去重候选/低价值降级/过期复审/类目错位检测 |
| ④归档员 | 分类法则（`pulse-path/references/classification_rules.md`） | 规则驱动 | 生命周期流转（L1→L2→归档） |
| ⑤审计员 | `auditor.py` | 每周 | 类目遵守/跨载体重复/指针断链 |
| ⑥守护员 | 进程守护脚本 | 每 1-2 分钟 | 崩溃自愈 |

权限三层（业界共识）：**全自动** = 抽取 / 体检低级修 / 守护 / 规则驱动降级；**半自动需审批** = 巩固结果 / 技能合并；**留人决策** = 删红线 / 跨载体重构 / 治理阈值本身。

## ⚠️ 检测脚本的退出码：必须 0

**教训**：检测脚本"完成检测但有异常"若返回 `exit 1`，调度器会把它记为**脚本失败**（`Result: FAILED`），即使检测本身正常完成、报告也正常输出（预警被 failed 掩盖）。

正确设计：**问题经 stdout 传递**——
- 有异常 → 输出报告（调度器把非空 stdout 投递/落盘）
- 正常 → 零输出（静默）
- 一律 `exit 0`（检测完成即成功）

一旦某个脚本改成恒 0，`last_status=error` 就不再是"有预警"的标志，排查时别把它当设计解释。

## consolidator.py（整理员）

- 只读检测，**只报告不自动改**（降级/删除动作需审批）
- 检测项：水位 / 降级候选（长叙述 > 阈值 + 有 L2 兜底线索）/ 重复检测（前缀相同）/ 过期复审（带日期且超过 retention_days）/ 类目错位（MEMORY 里出现用户偏好类 → 建议归 USER）
- 报告落盘：`$HERMES_HOME/reports/memory-consolidation-report.md`
- 用法：`python consolidator.py`（默认静默，异常才输出）/ `--report`（强制写报告）/ `--help`
- **阈值声明式化**：`mem_rules.json` 的 `consolidation` 段（`demote_min_len` / `dup_prefix` / `retention_days` / `project_prefixes`），改阈值只改规则表不动引擎

## auditor.py（审计员）

- 内容级审计：类目遵守（MEMORY 身份/偏好 → 归 USER）/ 跨载体重复（结构化事实库内容前缀与 MEMORY/USER 段相同 → 删）/ 指针 target 存在性（skill:// 查顶层+分类子目录，doc:// 查路径）/ ticket→入库链路核对
- 报告落盘：`$HERMES_HOME/reports/memory-audit-report.md`
- 实战价值：首跑即抓到结构化事实库条目与 MEMORY 正文重复 → 删

## 结构化事实库退役判据

- 事实库内容被语义库/MEMORY/USER/技能全量覆盖、停更、唯一消费者只剩审计脚本（只读）→ **可退役停用**
- 退役动作：先导出归档（落 `$HERMES_HOME/reports/`），再从写入侧摘除
- 退役前判定：确认无活跃写入方 + 无生产消费者（`grep -rln "<db 文件名>" scripts/*.py` 只剩审计脚本）后再归档停用

## 语义库 embedding 相关坑

1. **换 embedding 模型名会触发拒启**：collection 元数据与配置不匹配报 `EmbeddingRebuildRequiredError`。同权重同维度时，配置里加 `allow_metadata_override: true` 可保留现有向量免重建（例：同一模型只是 GPU/CPU 参数不同）。
2. **embedding 让道大型模型**：小 embedding 模型（~0.5B）CPU 跑足够（热态亚秒级，首载数秒冷启动）。用 `PARAMETER num_gpu 0` 建 CPU-only 副本，把显存腾给主模型。
3. **⚠️ 服务启停只走统一控制脚本**：禁手动后台拉进程（手动进程会退出）；守护脚本会自动自愈拉起标准无窗实例（这正是"守护员"角色的实战验证）。控制脚本 stop 可能连带停掉依赖服务，恢复要用官方启动方式。
4. 改 embedding 后跑 `ov doctor` 验证（Embedding PASS + dimension 对齐）。

## 分类法则（归属轴）要点

- **载体 = 知识形态 + 来源，不是主题**——主题只进分面标签，不进归属轴。这是根治"同一事实躺 3-4 处"的关键
- 完整表见 `pulse-path/references/classification_rules.md`（8 法则 + 载体归属表 + 生命周期 + 3 种合法参照：指针/继承/派生）
