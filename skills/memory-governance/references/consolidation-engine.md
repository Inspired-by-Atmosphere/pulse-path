# 治理引擎设计细节（体检员 / 整理员 / 审计员）

引擎全部**声明式规则表驱动**（`mem_rules.json`，新增规则不改代码）——"把补丁变成数据"。

## 三引擎分工

| 引擎 | 脚本 | 调度 | 职责 |
|---|---|---|---|
| 体检员 | `memory-pointer-system/scripts/mem_guard.py` | 每日 | 指针格式/存在性/水位，低级自动修，异常才投递通知 |
| 整理员 | `memory-pointer-system/scripts/consolidator.py` | 每日（睡眠期） | 水位/降级候选/重复/过期复审/类目错位，只读+报告落盘 |
| 审计员 | `memory-pointer-system/scripts/auditor.py` | 每周 | 分类法则遵守/跨载体重复/指针断链/ticket→入库链路 |

报告统一落 `$HERMES_HOME/reports/`（`memory-consolidation-report.md` / `memory-audit-report.md`）。调度用 `deliver=local` 不打扰（频繁打扰是敏感项）。

## 声明式规则表（mem_rules.json）

```json
{
  "watermark": { "threshold": 80, "files": [
      {"name": "MEMORY", "path": "${HERMES_HOME}/memories/MEMORY.md", "limit": 12000},
      {"name": "USER",   "path": "${HERMES_HOME}/memories/USER.md",   "limit": 4000}] },
  "consolidation": { "demote_min_len": 150, "dup_prefix": 20, "retention_days": 30,
                     "project_prefixes": [] }
}
```

- 阈值/文件清单全在规则表，引擎读 `rules.get(...)`，零业务 if
- **水位必须读规则表 `watermark.files`**（不要硬编码 limit——记忆上限扩容后硬编码会误报，实测踩过）
- 路径支持 `~` 与 `$VAR`；相对路径按 `HERMES_HOME` 解析（脚本内 `resolve_path()`）

## ⚠️ 调度环境下的退出码语义（关键坑）

- 检测型脚本**永远 exit 0**（检测完成即成功），问题经 stdout 传递
- 非零退出会被调度器标记为"脚本失败"（FAILED + 错误提醒）——实测整理员因水位 critical 返回 exit 1 → 调度器误报
- 有异常 → print 报告（`deliver=local` 落盘 / 配了渠道则投递）；全正常 → 静默

## 降级执行模式（"降级 ≠ 压缩"）

1. 内容写入 L2 结构体（语义库 write / 技能 / 文档）
2. L1 条目改指针：`key = scheme://target | 定位摘要`
3. 体检员复检 0 断链 + 整理员无分层建议
4. 信息找回验证：注入降级指针问内容，模型应能解析 target 答全

语义库 write 坑：URI 必须带 `default` 层级（漏了会静默失败，文件不创建）；新建文件要 `--mode create`（默认 replace 对不存在 URI 不建）；改 embedding 模型（维度不变）需配置 `allow_metadata_override`，否则拒启要求重建索引。

## 本地模型管理员能力测试（能否当整理员 VLM）

5 项（weak-model 下限标准，"垃圾模型都办得到就足够"）：
1. 载体判定：给信息判断归属（USER / AGENTS / 实体 / 事件 / 技能 / 文档）
2. 去重判断：两段是否同一事实（SSOT 核心）
3. identity / role / preferences 三层归类
4. 生命周期：L1 常驻 / L2 降级 / 销毁（噪音）
5. 结构化抽取：对话 → JSON（type/content/category）

判定：期望关键词命中即可。强项=去重/生命周期/抽取，弱项=载体归属模糊边界（可给规则表 + few-shot 补强）。实测一款 9B 级本地模型 ~12/15 达标，够当整理员。
