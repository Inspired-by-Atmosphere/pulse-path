# -*- coding: utf-8 -*-
"""审计员 auditor v1 —— 内容级记忆审计（每周）
检查分类法则遵守、跨载体重复、指针目标存在性。只读，报告落盘，异常才 stdout。
用法: python auditor.py [--report]
"""
import io
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes"))
DOCS_HOME = os.path.expanduser(os.environ.get("DOCS_HOME") or os.path.join("~", "Documents"))


def _p(env_name, default_rel):
    """环境变量优先；否则按 HERMES_HOME 拼相对路径。"""
    v = os.environ.get(env_name)
    return v if v else os.path.join(HERMES_HOME, default_rel)


MEMORY = _p("AUDIT_MEMORY", os.path.join("memories", "MEMORY.md"))
USER = _p("AUDIT_USER", os.path.join("memories", "USER.md"))
FACT_DB = _p("AUDIT_FACT_DB", "memory_store.db")
REPORT = _p("AUDIT_REPORT", os.path.join("reports", "memory-audit-report.md"))
SKILLS = _p("AUDIT_SKILLS", "skills")
VIKING_ROOT = os.path.expanduser(os.environ.get("VIKING_ROOT")
                                 or os.path.join("~", ".openviking", "data", "viking", "default",
                                                 "user", "default", "memories"))


def _resolve(p):
    """展开 ~/$VAR；相对路径按 HERMES_HOME 解析。"""
    if not p:
        return ""
    p = os.path.expandvars(os.path.expanduser(p))
    return p if os.path.isabs(p) else os.path.join(HERMES_HOME, p)


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


def verify_ticket_chain():
    """ticket→入库链路核对：status=processed 的 ticket，其 curator 写入的 viking 文件必须真实存在"""
    rules = {}
    try:
        rules = json.load(io.open(os.path.join(BASE, "mem_rules.json"), encoding="utf-8"))
    except Exception:
        pass
    tdir = _resolve(rules.get("curation", {}).get("ticket_dir")) or os.path.join(BASE, "memory_tickets")
    tfile = rules.get("curation", {}).get("ticket_file", "tickets.jsonl")
    tp = os.path.join(tdir, tfile)
    if not os.path.exists(tp):
        return []
    subdir_map = {"实体": "entities", "事件": "events", "偏好": "preferences"}
    broken = []
    processed = 0
    with io.open(tp, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                t = json.loads(ln)
            except Exception:
                continue
            if t.get("status") != "processed":
                continue
            processed += 1
            subdir = subdir_map.get(t.get("type", ""), "entities")
            base = (t.get("source") or t.get("content") or "memory")[:30]
            fname = re.sub(r'[\\/:*?"<>|\r\n]', "_", base) + ".md"
            p = os.path.join(VIKING_ROOT, subdir, fname)
            if not os.path.exists(p):
                broken.append((t.get("id", "?"), t.get("type", "?"), f"{subdir}/{fname}"))
    if processed == 0:
        return []
    if broken:
        return [(processed, broken)]
    return []


def read_file(p):
    try:
        with io.open(p, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def split_entries(text):
    entries, cur, start = [], [], 1
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.strip() == "§":
            if cur:
                entries.append((start, "\n".join(cur).strip()))
            cur, start = [], i + 1
        else:
            cur.append(ln)
    if cur:
        entries.append((start, "\n".join(cur).strip()))
    return entries


def fact_contents():
    try:
        conn = sqlite3.connect(FACT_DB)
        cur = conn.cursor()
        cur.execute("SELECT fact_id, content FROM facts")
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


USAGE = """auditor — 内容级记忆审计（只读，不修改任何数据）

检查: ①类目遵守（MEMORY/USER 归属） ②跨载体重复（fact_store vs 内置记忆）
      ③指针 target 存在性（skill:// / doc://） ④ticket→入库链路核对

用法:
  python auditor.py            # 静默模式：有问题才输出报告（调度器/定时任务用）
  python auditor.py --report   # 全正常时也写报告文件
  python auditor.py --help     # 本帮助

环境变量（默认值跨平台，不含个人路径）:
  HERMES_HOME      记忆库根目录，默认 ~/.hermes
  DOCS_HOME        文档根目录，默认 ~/Documents
  AUDIT_MEMORY     MEMORY.md  默认 $HERMES_HOME/memories/MEMORY.md
  AUDIT_USER       USER.md    默认 $HERMES_HOME/memories/USER.md
  AUDIT_FACT_DB    fact 库    默认 $HERMES_HOME/memory_store.db
  AUDIT_SKILLS     技能库根   默认 $HERMES_HOME/skills
  AUDIT_REPORT     报告落盘   默认 $HERMES_HOME/reports/memory-audit-report.md
  VIKING_ROOT      语义库根   默认 ~/.openviking/data/viking/default/user/default/memories

退出码: 恒 0（问题经 stdout 传递）
"""


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
        return 0
    mem = read_file(MEMORY)
    usr = read_file(USER)
    mem_entries = split_entries(mem)
    usr_entries = split_entries(usr)
    facts = fact_contents()
    issues = []  # (level, title, details)

    # 1. 类目遵守：MEMORY 中出现用户身份/偏好类（应归 USER）或项目角色（应归 AGENTS）
    mis = []
    for ln, c in mem_entries:
        if re.match(r"^[a-z_]+ = ", c):
            continue
        if any(k in c for k in ["用户硬性要求", "用户交互偏好", "用户明确", "交付偏好", "沟通"]):
            mis.append((ln, c[:50], "→USER"))
    if mis:
        issues.append(("warning", f"类目错位 {len(mis)} 条（MEMORY→USER）",
                       [f"  L{ln}: {head} {arrow}" for ln, head, arrow in mis]))

    # 2. 跨载体重复：fact_store 内容与 USER/MEMORY 段落高度相似（前 25 字相同）
    dup = []
    user_keys = {c[:25] for _, c in usr_entries if len(c) > 40}
    mem_keys = {c[:25] for _, c in mem_entries if len(c) > 40}
    for fid, content in facts:
        k = content[:25]
        where = "USER" if k in user_keys else ("MEMORY" if k in mem_keys else None)
        if where and len(content) > 40:
            dup.append((fid, where, content[:45]))
    if dup:
        issues.append(("warning", f"fact_store 与内置重复 {len(dup)} 条（退役方向：删）",
                       [f"  fact#{fid} ~ {where}: {head}" for fid, where, head in dup]))

    # 3. 指针 target 存在性（skill:// 查目录，doc:// 查路径）
    broken = []
    for ln, c in mem_entries:
        m = re.match(r"^[a-z_]+ = (\w+)://(\S+)", c)
        if m:
            scheme, target = m.group(1), m.group(2)
            if scheme == "skill":
                name = target.split("|")[0].strip()
                # 技能可能在 skills/<name>/ 顶层，或 skills/<category>/<name>/
                top = os.path.join(SKILLS, name)
                nested = ([os.path.join(SKILLS, d, name) for d in os.listdir(SKILLS)
                           if os.path.isdir(os.path.join(SKILLS, d))]
                          if os.path.isdir(SKILLS) else [])
                if not (os.path.isdir(top) or any(os.path.isdir(x) for x in nested)):
                    broken.append((ln, target[:40]))
            elif scheme == "doc":
                p = target.split("|")[0].replace("\\", "/").replace("//", "/")
                p = p[1:] if p.startswith("/") else p
                cand = [os.path.join(DOCS_HOME, p), os.path.join(HERMES_HOME, p), p]
                if not any(os.path.exists(x) for x in cand):
                    broken.append((ln, target[:40]))
    if broken:
        issues.append(("warning", f"指针疑似断链 {len(broken)} 条",
                       [f"  L{ln}: {t}" for ln, t in broken]))

    # 4. ticket→入库链路核对（curator 说 processed，viking 必须真有）
    tchain = verify_ticket_chain()
    if tchain:
        processed_n, broken_t = tchain[0]
        issues.append(("warning", f"ticket→入库链路断裂 {len(broken_t)}/{processed_n} 条",
                       [f"  ticket {tid} [{ttype}]: {path} 缺失" for tid, ttype, path in broken_t]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not issues:
        if "--report" in sys.argv:
            write_report(f"# 记忆审计报告 {now}\n\n全部正常，无异常项。\n")
            print(f"[auditor] 全正常，报告已写 {REPORT}")
        return 0

    lines = [f"# 记忆审计报告 {now}", ""]
    for level, title, details in issues:
        lines.append(f"## [{level}] {title}")
        lines.extend(details or ["  （无明细）"])
        lines.append("")
    text = "\n".join(lines)
    write_report(text)
    print(text)
    return 0  # 检测完成即成功；非零退出会被 cron 误报为脚本失败，问题经 stdout 传递


if __name__ == "__main__":
    sys.exit(main())
