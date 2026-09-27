# 记忆链健康体检 / "变笨·习惯消失"排查法

用户报"助手变笨 / 以前养成的习惯和策略消失了 / 该记的没记上"时，**根因常在治理流水线，不在模型**。先分清两条链路再动手。

## 一、先分清两条链路

| 链路 | 判活指标 |
|---|---|
| **输入链**（自动抽取 + 直写记忆工具） | 结构体数据最新写入时间 + `ov observer queue`（0 pending / 0 error = 健康） |
| **治理链**（ticket → curator → 结构体） | ticket 队列 + 决策登记簿 + 调度输出 + 结构体写入实据 |

## 二、治理链体检清单（五查，每步一条命令）

1. **ticket 队列**：`$HERMES_HOME/scripts/memory_tickets/tickets.jsonl`
   - pending 堆积 = curator 没在跑；大量 duplicate = 去重门可能误杀
2. **决策登记簿**：`$HERMES_HOME/_system/governance/decision-ledger.jsonl`
   - `pending_approval` 无结案 = 审批门搁置成孤儿；duplicate 证据指错类别 = 去重门 bug
3. **调度输出**：`$HERMES_HOME/cron/output/<job-id>/`
   - exit 127 + `C:Users…`（反斜杠被吞）= `.sh` 包装坑
4. **结构体写入实据**：`grep -rl "curator 写入" ~/.openviking/data/viking/default/user/default/memories/`
   - **空 = curator 从未成功写入过**（唯一写口形同虚设）
5. **写入活跃度**：`state.db` 按天统计直写记忆工具 / `memory_request` 调用数
   - ⚠️ `messages.timestamp` 是 **Unix 秒（float）非毫秒**——按 ms 过滤得假 0

## 三、关键判定

- 设计说"只产不写"但实际直写仍在发生 = **双轨不一致**，行为会乱（实测两者并存过）
- curator "端到端通过"验收标准 = **结构体实际出现写入且检索命中**，不是"不重复写/队列清空"
- 单一"唯一写口"管道的隐患：调度坏了 + 门误杀 + 审批搁置，三者任一都会让"该记的没记上"

## 四、一次完整根因链（实录，三个缺陷全命中）

一次大升级把记忆写入口改成"唯一写口 curator"，三个缺陷叠加导致记忆静默丢失：

1. **调度包装用 `.sh`** → Windows 反斜杠被吞 → exit 127，从建号起从未自动跑
2. **去重门"全库重叠≥2"** → 真实偏好被误杀（日期等数字 token 也拉高重叠）
3. **高危审批门**（内容含"删除/清理"即挂起）→ 请批通知不可靠 → 搁置成孤儿

修复（均已落地）：`.py` 包装 + 去重门收紧（同类别 / 较高重叠阈值 / 跳交接类文件 / 剔数字 token）+ 高危改"备份+自动写档"（`approval_policy: auto_with_backup`）。验收 = 事件票实际写入且检索命中（score 达标）。

## 五、附：决策登记簿记档方式

curator 高危/去重/写入决策经 `log_decision()` 追加记档（`proposer="记忆管理员(curator)"`）；政策变更、旧孤儿作废也走同一登记簿，用户"哪天想起来让汇报"时读 ledger 全回溯。
