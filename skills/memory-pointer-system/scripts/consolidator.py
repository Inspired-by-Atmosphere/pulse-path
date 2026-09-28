# -*- coding: utf-8 -*-
"""整理员 consolidation 引擎 v1 —— 睡眠期记忆巩固检测
只读 + 生成报告 + 低风险自动修。所有降级/删除动作只进报告（需审批），符合"先交审"铁律。
规则全部声明在 memory_rules.json / classification_rules.md，引擎零业务 if。
用法: python consolidator.py [--report]   # 默认静默，异常才输出；--report 强制出报告
"""
import io
import json
import os
import re
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
HERMES_HOME = os.path.abspath(os.path.expanduser(os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes")))
os.environ["HERMES_HOME"] = HERMES_HOME   # 规范化后写回环境：规则表里的 ${HERMES_HOME} 由 expandvars 展开，读到的是环境原值


def _p(env_name, default_rel):
    """环境变量优先；否则按 HERMES_HOME 拼相对路径。"""
    v = os.environ.get(env_name)
    return v if v else os.path.join(HERMES_HOME, default_rel)


def _resolve(p):
    """展开 ~/$VAR；相对路径按 HERMES_HOME 解析。"""
    if not p:
        return ""
    p = os.path.expandvars(os.path.expanduser(p))
    return p if os.path.isabs(p) else os.path.join(HERMES_HOME, p)


MEMORY = _p("CONSOLIDATE_MEMORY", os.path.join("memories", "MEMORY.md"))
USER = _p("CONSOLIDATE_USER", os.path.join("memories", "USER.md"))
# 分类法则表：默认取本仓库内 pulse-path 技能的 references（可用环境变量覆盖）
CLASS_RULES = os.environ.get("CONSOLIDATE_RULES_REF") or os.path.normpath(
    os.path.join(BASE, "..", "..", "pulse-path", "references", "classification_rules.md"))
RULES = os.path.join(BASE, "mem_rules.json")
REPORT = _p("CONSOLIDATE_REPORT", os.path.join("reports", "memory-consolidation-report.md"))


def write_report(text):
    """写报告文件（自动建目录）；失败不致命。"""
    try:
        d = os.path.dirname(REPORT)
        if d:
            os.makedirs(d, exist_ok=True)
        with io.open(REPORT, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception:
        return False


def load_rules():
    try:
        with io.open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def read_file(p):
    try:
        with io.open(p, encoding="utf-8", errors="ignore") as f:
            return f.read().replace("\r", "")
    except Exception:
        return ""


def split_entries(text):
    """按 § 分段，返回 [(行号, 内容), ...]"""
    entries = []
    cur = []
    start = 1
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.strip() == "§":
            if cur:
                entries.append((start, "\n".join(cur).strip()))
            cur = []
            start = i + 1
        else:
            cur.append(ln)
    if cur:
        entries.append((start, "\n".join(cur).strip()))
    return entries


def find_demote_candidates(memory_entries, min_len=150):
    """降级候选：长叙述（>100字）+ 非指针格式 + 含技能/viking/文档兜底线索"""
    candidates = []
    for lineno, content in memory_entries:
        if len(content) > min_len and not re.match(r"^[a-z_]+ = ", content):
            has_fallback = any(k in content for k in ["技能", "viking", "详见", "见 ", "doc://", "skill://"])
            if has_fallback:
                candidates.append((lineno, len(content), content[:60]))
    return candidates


def find_duplicates(entries):
    """重复检测：前 20 字相同或高度相似的两段"""
    dups = []
    seen = {}
    for lineno, content in entries:
        key = content[:20]
        if key in seen and len(content) > 30:
            dups.append((seen[key], lineno, key))
        seen[key] = lineno
    return dups


def find_stale(entries, rules):
    """过期复审：带 8/xx 或 2026-xx 日期且超过 retention_days 的非指针条目"""
    days = rules.get("retention_days", 30)
    stale = []
    now = datetime.now()
    for lineno, content in entries:
        if re.match(r"^[a-z_]+ = ", content):
            continue  # 指针跳过
        m = re.search(r"\(?(8/\d{1,2}|2026-\d{1,2}-\d{1,2})", content)
        if m:
            date_str = m.group(1)
            try:
                if "/" in date_str:
                    md = datetime(2026, 8, int(date_str.split("/")[1]))
                else:
                    md = datetime.strptime(date_str, "%Y-%m-%d")
                age = (now - md).days
                if age > days:
                    stale.append((lineno, age, content[:50]))
            except Exception:
                pass
    return stale


def check_watermark(text, limit):
    return len(text) > limit * rules_wm(limit) / 100 if False else (len(text), limit)


def rules_wm(default):
    return default


USAGE = """consolidator — 睡眠期记忆巩固检测（只读 + 报告 + 低风险自动修）

检查: ①水位（mem_rules.json watermark.files） ②降级候选（长叙述+有 L2 兜底）
      ③重复检测 ④过期复审 ⑤类目建议（MEMORY→USER）

用法:
  python consolidator.py            # 静默模式：有问题才输出报告
  python consolidator.py --report   # 全正常时也写报告文件
  python consolidator.py --help     # 本帮助

环境变量（默认值跨平台，不含个人路径）:
  HERMES_HOME          记忆库根目录，默认 ~/.hermes
  CONSOLIDATE_MEMORY   MEMORY.md 默认 $HERMES_HOME/memories/MEMORY.md
  CONSOLIDATE_USER     USER.md   默认 $HERMES_HOME/memories/USER.md
  CONSOLIDATE_RULES_REF 分类法则表，默认本仓库 skills/pulse-path/references/classification_rules.md
  CONSOLIDATE_REPORT   报告落盘 默认 $HERMES_HOME/reports/memory-consolidation-report.md

退出码: 恒 0（问题经 stdout 传递）
"""


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        return 0
    rules = load_rules()
    wm = rules.get("watermark", {})
    warn = wm.get("warning", 80)
    crit = wm.get("critical", 95)
    issues = []  # (级别, 标题, 明细行)

    # 1. 水位（读 mem_rules.json watermark.files，与 mem_guard 同源）
    wm_files = rules.get("watermark", {}).get("files", [])
    if not wm_files:
        wm_files = [{"name": "MEMORY", "path": MEMORY, "limit": 6000},
                    {"name": "USER", "path": USER, "limit": 2375}]
    for f in wm_files:
        text = read_file(_resolve(f.get("path")) or MEMORY)
        pct = len(text) / f["limit"] * 100
        level = "critical" if pct >= crit else ("warning" if pct >= warn else "ok")
        if level != "ok":
            flag = "🔴 水位告急需整理" if level == "critical" else "⚠️ 超阈值，查看分层建议"
            issues.append((level, f"水位 {f['name']} {len(text)}/{f['limit']} ({pct:.0f}%) {flag}", []))

    # 2. 降级候选
    mem_entries = split_entries(read_file(MEMORY))
    usr_entries = split_entries(read_file(USER))
    cands = find_demote_candidates(mem_entries, rules.get("consolidation", {}).get("demote_min_len", 150))
    if cands:
        details = [f"  L{ln} ({n}字): {head}" for ln, n, head in cands]
        issues.append(("warning", f"分层建议 {len(cands)} 条（长叙述+有L2兜底，应降级到技能/viking只留指针，非压缩）", details))

    # 3. 重复检测
    dups = find_duplicates(mem_entries + usr_entries)
    if dups:
        details = [f"  L{a} ~ L{b}: {key}…" for a, b, key in dups]
        issues.append(("warning", f"疑似重复 {len(dups)} 组", details))

    # 4. 过期复审
    stale = find_stale(mem_entries, rules)
    if stale:
        details = [f"  L{ln} ({age}天前): {head}" for ln, age, head in stale]
        issues.append(("warning", f"过期复审 {len(stale)} 条", details))

    # 5. 类目遵守（MEMORY 里出现用户身份/偏好类内容 → 提示归 USER）
    # 项目前缀（如某项目定稿名）在规则表 consolidation.project_prefixes 声明，命中即豁免（属项目条目，不归 USER）
    usr_keywords = ["用户硬性要求", "用户交互偏好", "用户明确", "交付偏好", "沟通"]
    project_prefixes = rules.get("consolidation", {}).get("project_prefixes", [])
    misplaced = []
    for lineno, content in mem_entries:
        if re.match(r"^[a-z_]+ = ", content):
            continue
        if any(k in content for k in usr_keywords) and not any(
                p and p in content[:10] for p in project_prefixes):
            misplaced.append((lineno, content[:50]))
    if misplaced:
        details = [f"  L{ln}: {head}" for ln, head in misplaced]
        issues.append(("warning", f"类目建议（MEMORY→USER）{len(misplaced)} 条", details))

    # 输出
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not issues:
        # 全正常：可选写报告，stdout 静默（cron 不投递）
        if "--report" in sys.argv:
            write_report(f"# 记忆巩固报告 {now}\n\n全部正常，无异常项。\n")
            print(f"[consolidator] 全正常，报告已写 {REPORT}")
        return 0

    lines = [f"# 记忆巩固报告 {now}", ""]
    for level, title, details in issues:
        lines.append(f"## [{level}] {title}")
        lines.extend(details or ["  （无明细）"])
        lines.append("")
    report_text = "\n".join(lines)

    write_report(report_text)
    # stdout：有异常就输出报告（cron 投递/落盘），无异常静默
    print(report_text)
    return 0  # 检测完成即成功；非零退出会被 cron 误报为脚本失败，问题经 stdout 传递


if __name__ == "__main__":
    sys.exit(main())
