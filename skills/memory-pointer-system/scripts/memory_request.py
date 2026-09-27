# -*- coding: utf-8 -*-
"""memory_request.py — 会话发起者 → ticket 队列（只产不写）
会话 agent 要写记忆时，用本脚本提交 ticket，不直接写入 viking/MEMORY/USER。
由 curator.py（记忆管理员，唯一写口）消化：质量门 → 归位 → 入库。
用法:
  python memory_request.py --type 实体 --content "..." --source "会话主题"
  python memory_request.py --type 事件 --content "..." --source "某项目"
  python memory_request.py --type 偏好 --content "..." --source "用户偏好"
  python memory_request.py --type L1 --key model --content "摘要" --source "..."
  python memory_request.py --list        # 查看待处理 ticket
type: 实体|事件|偏好|L1|技能
"""
import argparse
import io
import json
import os
import sys
import uuid
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes"))


def resolve_path(p):
    """展开 ~/$VAR；相对路径按 HERMES_HOME 解析。空值返回 ''。"""
    if not p:
        return ""
    p = os.path.expandvars(os.path.expanduser(p))
    return p if os.path.isabs(p) else os.path.join(HERMES_HOME, p)


RULES = os.path.join(BASE, "mem_rules.json")
VALID_TYPES = ["实体", "事件", "偏好", "L1", "技能"]


def load_rules():
    try:
        return json.load(io.open(RULES, encoding="utf-8"))
    except Exception:
        return {}


def ticket_path(rules):
    d = resolve_path(rules.get("curation", {}).get("ticket_dir")) or os.path.join(BASE, "memory_tickets")
    f = rules.get("curation", {}).get("ticket_file", "tickets.jsonl")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f)


def submit(t, content, source, key=None):
    rules = load_rules()
    path = ticket_path(rules)
    tid = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    ticket = {
        "id": tid,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": t,
        "content": content,
        "source": source or "",
        "key": key,
        "status": "pending",
    }
    with io.open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ticket, ensure_ascii=False) + "\n")
    return tid


def main():
    parser = argparse.ArgumentParser(description="记忆发起者 → ticket（只产不写）")
    parser.add_argument("--type", choices=VALID_TYPES)
    parser.add_argument("--content")
    parser.add_argument("--source", default="")
    parser.add_argument("--key", default=None)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        rules = load_rules()
        path = ticket_path(rules)
        pend = []
        if os.path.exists(path):
            with io.open(path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        try:
                            t = json.loads(ln)
                        except Exception:
                            continue
                        if t.get("status") == "pending":
                            pend.append(t)
        if not pend:
            print("无待处理 ticket")
        else:
            for t in pend:
                print(f'  [{t["id"]}] {t["type"]} | {t["content"][:40]} | src={t["source"]}')
            print(f'  待处理 {len(pend)} 条')
        return 0

    if not args.type or not args.content:
        print("用法: python memory_request.py --type 实体|事件|偏好|L1|技能 --content ... [--source ...] [--key ...]")
        return 1
    if len(args.content) < 5:
        print("内容太短（<5字），不提交")
        return 1

    tid = submit(args.type, args.content, args.source, args.key)
    print(f"✓ ticket 已提交 {tid}（{args.type}），等记忆管理员 curator 处理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
