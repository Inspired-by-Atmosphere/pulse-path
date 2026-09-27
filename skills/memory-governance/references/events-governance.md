# 自动抽取产物治理（`curator --govern-events`）

背景：会话末自动抽取每天产几十条事件，写入时未过 curator 质量门（自动抽取是 Background 主路径，直接落库）。`--govern-events` 让 curator 对自动抽取产物做治理，不再只等 ticket。

## 机制

- 参数全声明式在 `mem_rules.json` `curation.events_governance`：`enabled / window_days / min_len_keep / dedup_min_overlap / dedup_min_similarity / stopwords / dedup_skip_files / write_mode`，引擎零业务 if
- 三查：①准入（过短/噪音词 → 疑似低质）②去重（与同类其他事件文件双条件重叠 → 疑似重复）③类目（events 根下非 `YYYY/MM/DD` 结构文件 → 类目错位）
- `write_mode: report_only`：只报告不删改（ADD-only 保守默认），处置由用户拍板；每条发现记决策登记簿（`type=ticket-events-governance`）

## 去重双条件（防误报核心）

抽取产物事件文件几百字，模板结构词（Summary/ChatLog/memory/type/source/id/name/event 等）每篇都有 → 照搬 ticket 去重的"重叠≥N"必全误报（任意两篇天然重叠 20+ 词）。光加停用词是打地鼠——滤一批又浮一批环境词（路径/AI/Users/标题/格式…）。

正解 = **绝对重叠 + 相似度比例双条件**：`overlap ≥ dedup_min_overlap 且 overlap/min(双方词数) ≥ dedup_min_similarity`（默认 0.3）。实测：真重复（例行事件 0.52/0.54）保留，跨主题误报（0.05/0.03）滤除——**比例判据才是关键，绝对数对长文档无效**。

## 参数调优要点

- `stopwords` 三来源：中文通用词（信息/文件/处理/完成/更新/同步…）+ 英文结构词（Summary/ChatLog/memory/type/source/id…）+ 本机环境词（AI/Users/路径/格式/标题…）
- `dedup_skip_files` 同时作用于 **targets 收集（治理对象）** 和 **去重对比对象**——**两处都要过滤**，只过滤对比对象会漏（治理对象含 `.overview` 仍被报）
- `.overview.md`（每目录自动生成的目录概述）必须进 skip，内容与同主题事件天然重叠
- **例行性重复事件豁免**：每天写高度模板化产物的定时任务（如信息管道类），确认后加入 skip，避免天天误报噪音；历史记录一字不动（日志类铁律）

## 类目归位

events 根下非 `YYYY/MM/DD` 结构的文件 = 历史类目错位 → 用 `ov mv <src-uri> <dst-uri>` 移动（走 CLI 会同步向量索引）；直接动文件系统则检索仍取旧路径数据。移完用 `ov ls` 验证源消失/目标命中。

## 调度接线

调度包装脚本调用 `curator.py --commit --govern-events`（每日一轮，与 ticket 消化同轮）。首次上线先 dry-run 看报告，调好阈值再进调度。
