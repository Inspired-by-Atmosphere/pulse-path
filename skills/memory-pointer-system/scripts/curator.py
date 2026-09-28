# -*- coding: utf-8 -*-
"""curator.py — 记忆管理员（唯一写口）
消化 memory_tickets/ 队列：质量门(价值/去重/分类) → 归位 → 写入 viking(L2) + L1 指针建议。
会话 agent 只发 ticket（memory_request.py），写入只经本脚本。
谨慎设计：默认 dry-run 只分析，--commit 才真正写 viking；L1/技能 不自动写（报告建议）。
用法:
  python curator.py              # dry-run 分析 ticket 队列
  python curator.py --commit     # 质量门通过 → ov write 入库 viking
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
HERMES_HOME = os.path.abspath(os.path.expanduser(os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes")))
os.environ["HERMES_HOME"] = HERMES_HOME   # 规范化后写回环境：规则表里的 ${HERMES_HOME} 由 expandvars 展开，读到的是环境原值


def _p(env_name, default_rel):
    """环境变量优先；否则按 HERMES_HOME 拼相对路径。"""
    v = os.environ.get(env_name)
    return v if v else os.path.join(HERMES_HOME, default_rel)


RULES = os.path.join(BASE, "mem_rules.json")
# 外部语义库 CLI（如 OpenViking `ov`）。未安装时本脚本仍可 dry-run 分析，只是无法 --commit 入库。
OV = os.environ.get("OV_CLI") or os.path.join(HERMES_HOME, "bin", "ov.exe")
VIKING_ROOT = os.path.expanduser(os.environ.get("VIKING_ROOT")
                                 or os.path.join("~", ".openviking", "data", "viking", "default",
                                                 "user", "default", "memories"))
REPORT = _p("CURATE_REPORT", os.path.join("reports", "memory-curation-report.md"))
# 可选：外部治理引擎目录（提供 log_decision / create_approval_request）。默认不加载；
# 设 GOVERNANCE_MODULE_DIR 指向含 core.py 的目录即启用。缺失时决策登记静默降级（不影响入库）。
GOV_DIR = os.environ.get("GOVERNANCE_MODULE_DIR") or ""
if GOV_DIR and os.path.isdir(GOV_DIR):
    sys.path.insert(0, GOV_DIR)
HIGH_RISK_WORDS = ["删除", "重构", "覆盖", "替换", "清理"]  # 高危动作需审批，不自动执行


def _gov():
    """延迟 import 治理引擎（core.py），避免环境缺失时崩掉 curator"""
    try:
        import core as govcore
        return govcore
    except Exception:
        return None


def log_ticket_decision(t, result, action, reason):
    """记入决策登记簿（追加式，治理引擎 log_decision）"""
    gov = _gov()
    if gov is None:
        return None
    try:
        return gov.log_decision({
            "proposer": "记忆管理员(curator)",
            "type": f"ticket-{t.get('type', '?')}",
            "result": result,
            "action": action,
            "reason": reason,
        })
    except Exception:
        return None


def request_approval(t, reason):
    """高危动作 → 审批门挂起（create_approval_request），不自动执行"""
    gov = _gov()
    if gov is None:
        return None
    try:
        req = gov.create_approval_request(
            title=f"记忆高危操作: {t.get('type', '?')}",
            description=f"ticket {t.get('id')}: {t.get('content', '')[:60]} | 原因: {reason}",
        )
        return req.get("decision_id") if isinstance(req, dict) else None
    except Exception:
        return None


def load_rules():
    try:
        return json.load(io.open(RULES, encoding="utf-8"))
    except Exception:
        return {}


def resolve_path(p):
    """展开 ~/$VAR；相对路径按 HERMES_HOME 解析。空值返回 ''。"""
    if not p:
        return ""
    p = os.path.expandvars(os.path.expanduser(p))
    return p if os.path.isabs(p) else os.path.join(HERMES_HOME, p)


def ticket_path(rules):
    d = resolve_path(rules.get("curation", {}).get("ticket_dir")) or os.path.join(BASE, "memory_tickets")
    f = rules.get("curation", {}).get("ticket_file", "tickets.jsonl")
    return os.path.join(d, f)


def read_tickets(rules):
    p = ticket_path(rules)
    tickets = []
    if os.path.exists(p):
        with io.open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        tickets.append(json.loads(ln))
                    except Exception:
                        pass
    return tickets


def save_tickets(rules, tickets):
    p = ticket_path(rules)
    with io.open(p, "w", encoding="utf-8") as f:
        for t in tickets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")


def quality_value(content, rules):
    """门1 价值鉴定：长度 + 噪音词（保守默认：低价值不进库）"""
    gate = rules.get("curation", {}).get("quality_gate", {})
    min_len = gate.get("min_len_keep", 10)
    noise = gate.get("noise_keywords", [])
    if len(content) < min_len:
        return "discard", "过短"
    if any(k in content for k in noise):
        return "discard", "噪音关键词"
    return "pass", ""


def quality_dedup(t, rules):
    """门2 去重（2026-08-27 修正）：同类别目录内扫 viking 源文件，
    关键词重叠 ≥ dedup_min_overlap（默认4）且文件名不匹配 skip 清单才判重复。
    原版全库重叠≥2 太宽（「双格式交付」偏好被误杀），参数已声明式化（mem_rules.json）。"""
    gate = rules.get("curation", {}).get("quality_gate", {})
    prefix = gate.get("dedup_prefix", 20)
    min_overlap = gate.get("dedup_min_overlap", 4)
    skip = gate.get("dedup_skip_files", [])
    same_cat = gate.get("dedup_same_category_only", True)
    content = t.get("content", "")
    # 2026-08-27 修正：首尾各取 prefix 分开 tokenize（消除拼接缝伪合并），并剔除纯数字 token（日期/编号无语义，防假重叠）
    words = set()
    for part in (content[:prefix], content[-prefix:]):
        words |= {w for w in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", part) if not w.isdigit()}
    if not words:
        return []
    subdir = classify_type(t.get("type", ""))
    hits = []
    for root, _dirs, files in os.walk(VIKING_ROOT):
        if same_cat:
            rel = os.path.relpath(root, VIKING_ROOT)
            if rel.split(os.sep)[0] != subdir:
                continue
        for fn in files:
            if not fn.endswith(".md"):
                continue
            if any(s in fn for s in skip):
                continue
            p = os.path.join(root, fn)
            try:
                txt = io.open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            overlap = len(words & set(re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", txt)))
            if overlap >= min_overlap:
                hits.append(os.path.relpath(p, VIKING_ROOT))
                if len(hits) >= 3:
                    return hits
    return hits


def classify_type(t):
    """门3 分类：type → viking 类别子目录（实体/事件/偏好）；L1/技能 不走 viking 自动写"""
    mapping = {"实体": "entities", "事件": "events", "偏好": "preferences"}
    return mapping.get(t, "entities")


def _tokenize(text, stopwords=None):
    """抽中文/英文/数字词元（剔纯数字 + 停用词），供重叠检测"""
    words = {w for w in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", text) if not w.isdigit()}
    if stopwords:
        words -= set(stopwords)
    return words


def govern_events(rules):
    """治理自动抽取产物（对齐 OpenClaw Dreaming REM/Deep，2026-09-25 新增）。

    背景：SessionCommit 自动抽取每天产几十条 events，但写入时未过 curator 质量门。
    本函数对最近 window_days 天的自动抽取产物做三查：
      ① 准入  — 过短/噪音词 → 疑似低质
      ② 去重  — 与 events 类其他文件高重叠 → 疑似重复
      ③ 类目  — events 根下非日期结构文件 → 类目错位
    首版 write_mode=report_only（ADD-only 保守默认，不删不改），全部由用户拍板处置。
    参数声明式配置于 mem_rules.json curation.events_governance，改配置不碰引擎。
    """
    cfg = rules.get("curation", {}).get("events_governance", {})
    if not cfg.get("enabled", False):
        return []
    window = cfg.get("window_days", 1)
    min_len = cfg.get("min_len_keep", 40)
    min_overlap = cfg.get("dedup_min_overlap", 8)
    min_similarity = cfg.get("dedup_min_similarity", 0.3)
    skip = cfg.get("dedup_skip_files", [])
    noise = cfg.get("noise_keywords", [])
    stopwords = cfg.get("stopwords", [])
    base = os.path.join(VIKING_ROOT, "events")

    # 最近 window 天的日期目录下的抽取产物（跳过 skip 清单，如 .overview）
    today = datetime.now().date()
    targets = []
    for i in range(window):
        d = today - timedelta(days=i)
        p = os.path.join(base, str(d.year), "%02d" % d.month, "%02d" % d.day)
        if os.path.isdir(p):
            for fn in sorted(os.listdir(p)):
                if fn.endswith(".md") and not any(s in fn for s in skip):
                    targets.append(os.path.join(p, fn))

    findings = []
    for p in targets:
        rel = os.path.relpath(p, VIKING_ROOT)
        try:
            txt = io.open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        body = txt.strip()
        # ① 准入
        if len(body) < min_len:
            findings.append((rel, "疑似低质", "仅 %d 字符（< %d）" % (len(body), min_len)))
            continue
        bad = [k for k in noise if k in body][:2]
        if bad:
            findings.append((rel, "疑似噪音", "含关键词: %s" % ", ".join(bad)))
            continue
        # ② 去重（与 events 类其他文件高重叠，跳过自身与 skip 清单）
        words = _tokenize(body, stopwords)
        if len(words) < min_overlap:
            continue
        hits = []
        for root, _dirs, files in os.walk(VIKING_ROOT):
            if os.path.relpath(root, VIKING_ROOT).split(os.sep)[0] != "events":
                continue
            for fn in files:
                if not fn.endswith(".md"):
                    continue
                fp = os.path.join(root, fn)
                if os.path.abspath(fp) == os.path.abspath(p):
                    continue
                if any(s in fn for s in skip):
                    continue
                try:
                    other = io.open(fp, encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                ow = _tokenize(other, stopwords)
                ov = len(words & ow)
                # 2026-09-25：长事件文件用「绝对重叠 ≥ 阈值 且 相似度比例 ≥ 阈值」双条件，
                # 防通用词（路径/源名/环境词）堆出假重叠
                denom = min(len(words), len(ow)) or 1
                if ov >= min_overlap and ov / denom >= min_similarity:
                    hits.append(os.path.relpath(fp, VIKING_ROOT))
                    if len(hits) >= 3:
                        break
        if hits:
            findings.append((rel, "疑似重复", "命中: %s" % "; ".join(hits[:2])))
    # ③ 类目：events 根下非日期结构文件（历史错位）
    if os.path.isdir(base):
        for fn in sorted(os.listdir(base)):
            if fn.endswith(".md") and not any(s in fn for s in skip):
                findings.append((os.path.join("events", fn), "类目错位", "位于 events 根目录（非 YYYY/MM/DD 结构）"))
    return findings


def write_viking(t, rules, dry_run=True):
    """写入 viking（唯一写口）。实体/事件/偏好 → ov write 到对应类别目录"""
    subdir = classify_type(t["type"])
    if subdir not in ("entities", "events", "preferences"):
        return "L1/技能 不走 viking 自动写（L1=报告建议）"
    # 2026-09-26：事件进日期目录 events/YYYY/MM/DD/（与 OV 自动抽取一致，防 events 根堆积=类目错位）
    if subdir == "events":
        d = datetime.now()
        subdir = os.path.join("events", str(d.year), "%02d" % d.month, "%02d" % d.day)
    base_name = (t.get("source") or t.get("content") or "memory")[:30]
    fname = re.sub(r'[\\/:*?"<>|\r\n]', "_", base_name) + ".md"
    uri = f"viking://user/default/memories/{subdir}/{fname}"
    tmp = os.path.join(BASE, "_curator_tmp.md")
    body = f"# {base_name}\n\n{t['content']}\n\n<!-- 记忆管理员 curator 写入 | source: {t.get('source')} | {t['ts']} -->\n"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    if dry_run:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return f"[dry-run] 将写入 {uri}"
    try:
        r = subprocess.run(
            [OV, "write", uri, "--from-file", tmp, "--mode", "create", "--wait", "--timeout", "120"],
            capture_output=True, text=True, encoding="utf-8", timeout=150,
        )
    except Exception as e:
        return f"✗ 写入异常: {e}"
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
    if r.returncode == 0:
        return f"✓ 已写入 {uri}"
    return f"✗ 写入失败: {r.stderr[:120]}"


def backup_target_if_exists(t, rules):
    """高危动作前备份 viking 已有同名目标（若存在）到 memory_tickets/backup/，返回备份说明。
    2026-08-27 用户决策：不再依赖请批通知（会搁置），自行备份后自动执行写档。"""
    subdir = classify_type(t.get("type", ""))
    if subdir not in ("entities", "events", "preferences"):
        return ""
    base_name = (t.get("source") or t.get("content") or "memory")[:30]
    fname = re.sub(r'[\\/:*?"<>|\r\n]', "_", base_name) + ".md"
    src = os.path.join(VIKING_ROOT, subdir, fname)
    if not os.path.exists(src):
        return ""
    bkdir = os.path.join(BASE, "memory_tickets", "backup")
    try:
        os.makedirs(bkdir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(bkdir, f"{ts}_{fname}")
        shutil.copy2(src, dst)
        return f" | 已备份旧文件→{dst}"
    except Exception as e:
        return f" | 备份失败: {e}"


def main():
    parser = argparse.ArgumentParser(description="记忆管理员（唯一写口）：消化 ticket 队列 + 治理自动抽取产物")
    parser.add_argument("--commit", action="store_true", help="真正写入 viking（默认 dry-run）")
    parser.add_argument("--govern-events", action="store_true", help="治理自动抽取产物（report_only，不删改）")
    args = parser.parse_args()

    rules = load_rules()
    tickets = read_tickets(rules)
    pending = [t for t in tickets if t.get("status") == "pending"]
    results = []

    for t in pending:
        # 高危检测（删除/重构/覆盖）：2026-08-27 用户决策——不再挂审批门（请批通知不可靠会搁置），
        # 改为「自行备份已有目标 → 自动写档 → 决策登记簿记档」，用户随时可让汇报。
        if t.get("type") in ("删除", "重构") or any(w in t["content"] for w in HIGH_RISK_WORDS):
            bk = backup_target_if_exists(t, rules)
            w = write_viking(t, rules, dry_run=not args.commit)
            t["status"] = "processed" if args.commit else "pending"
            log_ticket_decision(t, t["status"], "write_viking(备份+自动)", w + bk)
            results.append((t, t["status"], f"高危操作→备份+自动写档 {bk} | {w}"))
            continue
        # 门1 价值鉴定
        v, why = quality_value(t["content"], rules)
        if v == "discard":
            t["status"] = "discarded"
            log_ticket_decision(t, "discarded", "discard", why)
            results.append((t, "discard", why))
            continue
        # 门2 去重
        hits = quality_dedup(t, rules)
        if hits:
            t["status"] = "duplicate"
            log_ticket_decision(t, "duplicate", "skip", "疑似重复: " + "; ".join(hits[:2]))
            results.append((t, "duplicate", "疑似重复: " + "; ".join(hits[:2])))
            continue
        # 门3 分类 + 写入
        w = write_viking(t, rules, dry_run=not args.commit)
        if args.commit:
            t["status"] = "processed"
        log_ticket_decision(t, t["status"], "write_viking" if args.commit else "dry-run", w)
        results.append((t, "processed" if args.commit else "pending", w))

    save_tickets(rules, tickets)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# 记忆管理员 curator 报告 {now}", f"模式: {'COMMIT（提交模式，逐条结果见下）' if args.commit else 'DRY-RUN（只分析）'}", ""]
    for t, status, detail in results:
        lines.append(f"- [{status}] {t['type']} | {t['content'][:30]} → {detail}")
    if not results:
        lines.append("（无待处理 ticket）")
    # 自动抽取产物治理（report_only）
    govern_findings = []
    if args.govern_events:
        findings = govern_events(rules)
        govern_findings = findings
        lines.append("")
        lines.append("## 自动抽取产物治理（report-only，处置待用户拍板）")
        if findings:
            for rel, kind, detail in findings:
                lines.append(f"- [{kind}] {rel} → {detail}")
                log_ticket_decision(
                    {"id": "evg-" + rel, "type": "events-governance", "content": rel},
                    kind, "report", detail,
                )
        else:
            lines.append("（窗口内无异常）")
    text = "\n".join(lines)
    rd = os.path.dirname(REPORT)
    if rd:
        os.makedirs(rd, exist_ok=True)
    with io.open(REPORT, "w", encoding="utf-8") as f:
        f.write(text)
    # 通知策略：无活（无 ticket 结果且无治理发现）→ 静默零输出（定时任务不投递）；
    # 有活才 print（配了通知渠道时投递给用户，让用户知道有要办的事）
    if results or govern_findings:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
