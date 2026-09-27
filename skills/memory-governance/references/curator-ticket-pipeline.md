# 记忆管理员 curator 写入治理管线

角色分离：会话 agent 只产不写，curator（记忆管理员）唯一写口。业界背书：专职 Memory Curator Agent / MemoryHub 同构——worker 只发 event，专职 curator 独有写权（"durable memory must be earned"）。

## 流水线

```
会话 agent --memory_request.py--> ticket 队列(jsonl) --> curator.py --质量门--> 结构体入库
                                                                  --> 决策登记簿记档
                                                                  --> 高危 → 备份 + 自动写档
```

## 文件

| 脚本 | 职责 |
|---|---|
| `memory-pointer-system/scripts/memory_request.py` | 发起端：`--type 实体\|事件\|偏好\|L1\|技能 --content ... --source ... [--key ...]`，落 ticket 队列（jsonl，`status=pending`）。`--list` 查看待处理。内容 <5 字拒绝 |
| `memory-pointer-system/scripts/curator.py` | 唯一写口：默认 **dry-run** 只分析，`--commit` 才真写。质量门三道 → 结构体写入 → 决策登记簿记档 |
| `curator_cron.py`（自行编写） | 调度包装：调用 `curator.py --commit`。**必须用 `.py`，禁 `.sh`**（见下） |

## 质量门三道（声明式，`mem_rules.json` `curation` 段）

1. **门1 价值鉴定**：长度 ≥ `min_len_keep` 且非噪音关键词（`noise_keywords`）；否则 discard（记 ledger）
2. **门2 去重**：扫结构体源文件（本地文件遍历），**同类别目录 + 关键词重叠 ≥ `dedup_min_overlap` + 跳过 `dedup_skip_files`（交接包/overview/profile）+ 剔除纯数字 token** 才判 duplicate。⚠️ 早期"全库重叠≥2"太宽，曾误杀真实偏好
3. **门3 分类**：type → 类别子目录（实体→`entities` / 事件→`events` / 偏好→`preferences`）；L1/技能 不自动写（`l1_pointer_update: report_only`，L1 改动敏感留人决策）

## 高危动作策略

- 触发：内容含 删除/重构/覆盖/替换/清理，或 `type=删除|重构`
- 动作：**自行备份已有目标 → 自动写档 → 决策登记簿记档**（`approval_policy: auto_with_backup`）
- ⚠️ 早期设计是"挂审批门等用户批准"，实测请批通知不可靠 → 搁置成孤儿（`pending_approval` 无结案）。政策可直接由用户拍板变更，用户随时可让汇报
- 决策登记簿是**追加不可变**的 jsonl（`decision-ledger.jsonl`），记录 `{proposer, type, result, action, reason}`；可挂接外部治理模块（本仓库脚本通过环境变量 `GOVERNANCE_MODULE_DIR` 可选加载，缺失时静默降级、不影响入库）

## auditor 链路核对

`auditor.py` 检查：读 ticket 队列，`status=processed` 的 ticket 其结构体写入文件必须真实存在（按 curator 的 URI 规则重建：`<类别目录>/<source 或 content 前 30 字>.md`），缺失报 `ticket→入库链路断裂`。

## 业界调研要点（记忆管理员角色设计）

- 业界"谁写记忆"四形态：A 会话自写 → B 发起+平台写（mem0/OpenAI/Anthropic Dreaming）→ C 专用 curator 写（同构背书）→ D 权限/身份治理（read_only / read_write / RBAC）
- 共识：写回是新失败面（注入面）；保守默认 **ADD-only / 不发明新事实 / 丢弃优先**；写入判断交给有上下文的第二道工序；写动作可审计
- 质量门参考：档案领域 7 标准（价值鉴定，实测"仅 4-8% 保留"）+ 数据管理六维度（完整性/唯一性/时效性/有效性/准确性/一致性）

## 测试要点（谨慎，不污染真实记忆）

- 测试用明显"P0/P1测试"内容 + 完成后清空 ticket 队列 + 删除测试实体 + 清空 approval-state
- ledger 追加不可变，测试记录会留下（可接受）
- `&&` 链注意：`memory_request` 拒绝（如 <5 字）返回 exit 1 会断链，后续命令不执行
