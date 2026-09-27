# -*- coding: utf-8 -*-
"""
scan_ghost_script_refs.py v2 — 幽灵引用扫描（修正则 + 全盘二次确认）
=====================================================================
用途：文档（任务卡/指南书/知识手册/项目文档）里会写"跑 xxx.py"，但脚本可能已被删/移动。
本脚本抽出文档引用的所有 .py 名，再到"脚本可能真实存在的所有根"全盘查证——
**两处都不存在才算真幽灵**（只看某个 scripts/ 目录会把合法的异地脚本误报成幽灵）。

v1 → v2 修正（2026-08-19）：
  ① 正则误报：`download.pytorch.org` 里的 `download` + `.py` + `torch` 子串被当成 `download.py`
     → 修正则：`.py` 后加负向前瞻 `(?![A-Za-z0-9_.-])`，禁止文件名继续字符
  ② 存在性只比对单个 scripts/ 平级目录，漏掉 src/、app/ui/、webapp/server/、归档区等合法位置
     → 改为：候选幽灵在所有 search roots 全盘二次搜索，找到即排除（判为"引用有效"）

开源版通用化：目录不写死在脚本里，两条来源，命令行优先：
  1) 命令行：--docs-root / --zone / --plan-zone / --search-root / --skip
  2) 规则表：mem_rules.json → content_scans.ghost_scripts
     { target, zone_dirs[], plan_zone_dirs[], search_roots[], skip_dirs[], enabled }

用法:
  python scan_ghost_script_refs.py --help
  python scan_ghost_script_refs.py --docs-root ./docs --zone ./docs/tasks \
      --search-root ./src --search-root ../other-repo
  python scan_ghost_script_refs.py            # 用 mem_rules.json 里配置的目录

退出码: 0（结果经 stdout 传递，便于定时任务收集）
"""
import argparse
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(BASE, "mem_rules.json")

# 修正则：.py 后禁接文件名继续字符（防 download.pytorch.org → download.py 误报）
PY_REF = re.compile(r"([A-Za-z0-9_\-]+\.py)(?![A-Za-z0-9_.\-])")
DEFAULT_SKIP = ("03_交付区", "06_简报")


def load_rules():
    try:
        with io.open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def collect_refs(zones, base):
    """扫描 zones 下所有 .md，收集引用的 .py。返回 {script: [(zone_label, relpath:line)]}"""
    found = {}
    for zone, label in zones:
        if not os.path.isdir(zone):
            continue
        for root, dirs, files in os.walk(zone):
            dirs[:] = [d for d in dirs if d not in (".git", ".venv", "node_modules", "__pycache__")]
            for f in files:
                if not f.endswith(".md"):
                    continue
                p = os.path.join(root, f)
                try:
                    with io.open(p, encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, 1):
                            for m in PY_REF.finditer(line):
                                rel = os.path.relpath(p, base) if base else p
                                found.setdefault(m.group(1), []).append((label, f"{rel}:{i}"))
                except Exception:
                    pass
    return found


def resolve_script(name, search_roots, limit=3):
    """全盘搜索脚本真实位置。返回 (存在?, 位置列表)。"""
    hits = []
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for r, d, fs in os.walk(root):
            # 跳过 .git / .venv / site-packages / node_modules / 备份目录
            d[:] = [x for x in d if x not in (".git", ".venv", "site-packages", "node_modules",
                                              "__pycache__") and not x.startswith("app_bak_")]
            if name in fs:
                hits.append(os.path.join(r, name))
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    return (bool(hits), hits[:limit])


USAGE = """scan_ghost_script_refs — 幽灵引用扫描（文档引用的 .py 是否真实存在）

判定: 文档引用的 .py 名，到所有 search roots 全盘查证；两处都不存在才算真幽灵。
      exec 区（任务卡/指南书/知识手册）的真幽灵 = 必须修；
      plan 区（规划性文档）的幽灵引用 = 未来规划/示例，需人工确认。

用法:
  python scan_ghost_script_refs.py [选项]

选项:
  --docs-root DIR     相对路径基准目录（报告里按它算相对路径）
  --zone DIR          执行区（可重复；缺失脚本算 A 类真幽灵）
  --plan-zone DIR     规划区（可重复；缺失脚本算 B 类，需人工确认）
  --search-root DIR   脚本可能存在的根（可重复）
  --skip NAME         跳过路径含该串的目录（可重复；默认 03_交付区 / 06_简报）
  --help              本帮助

配置来源（命令行优先，其次规则表）:
  mem_rules.json → content_scans.ghost_scripts.{target, zone_dirs, plan_zone_dirs,
                                                 search_roots, skip_dirs, enabled}
  两处都没配置时打印提示后退出（不报错）。

退出码: 0（结果经 stdout 传递）
"""


def main():
    rules_cfg = load_rules().get("content_scans", {}).get("ghost_scripts", {}) or {}
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--docs-root")
    ap.add_argument("--zone", action="append", default=[])
    ap.add_argument("--plan-zone", action="append", default=[])
    ap.add_argument("--search-root", action="append", default=[])
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--help", "-h", action="store_true")
    args = ap.parse_args()

    if args.help:
        print(USAGE)
        return 0

    docs_root = args.docs_root or rules_cfg.get("target", "")
    if not docs_root:
        print("未配置文档目录：用 --docs-root 指定，或写进 mem_rules.json "
              "content_scans.ghost_scripts.target（见 --help）。")
        return 0

    zones = []
    if args.zone or args.plan_zone:
        zones += [(z if os.path.isabs(z) else os.path.join(docs_root, z), "exec") for z in args.zone]
        zones += [(z if os.path.isabs(z) else os.path.join(docs_root, z), "plan") for z in args.plan_zone]
    else:
        zones += [(os.path.join(docs_root, z), "exec") for z in rules_cfg.get("zone_dirs", [])]
        zones += [(os.path.join(docs_root, z), "plan") for z in rules_cfg.get("plan_zone_dirs", [])]

    search_roots = args.search_root or [
        (r if os.path.isabs(r) else os.path.join(docs_root, r))
        for r in rules_cfg.get("search_roots", [])] or [docs_root]
    skip = tuple(args.skip or rules_cfg.get("skip_dirs", list(DEFAULT_SKIP)))

    zones = [(z, lbl) for z, lbl in zones if not any(s in z for s in skip)]
    if not zones:
        print("没有任何 zone 目录存在（检查 --zone / mem_rules.json 配置）。")
        return 0

    found = collect_refs(zones, docs_root)
    print(f"文档引用到的 .py 共 {len(found)} 个（修正则后）\n")
    print("=== 幽灵引用判定（全盘搜索后仍不存在的才是真幽灵）===")

    ghost_exec, ghost_plan, resolved = {}, {}, {}
    for s, locs in sorted(found.items()):
        exists, where = resolve_script(s, search_roots)
        for label, loc in locs:
            if exists:
                resolved.setdefault(s, []).append((where, loc))
            elif label == "exec":
                ghost_exec.setdefault(s, []).append(loc)
            else:
                ghost_plan.setdefault(s, []).append(loc)

    print("\n【已解决引用 —— 脚本真实存在，非幽灵】(全盘搜索命中)")
    if not resolved:
        print("  （无）")
    for s, items in sorted(resolved.items()):
        where = items[0][0]
        print(f"  ✅ {s}  →  存在于 {where[0] if where else '?'}（{len(items)} 处引用）")

    print(f"\n【A. 真幽灵 —— 执行区引用但全盘无此脚本，必须修】({len(ghost_exec)} 个)")
    if not ghost_exec:
        print("  （无）")
    for s, locs in sorted(ghost_exec.items()):
        print(f"\n  ❌ {s}:")
        for loc in locs[:8]:
            print(f"      - {loc}")

    print(f"\n【B. 规划区的幽灵引用 —— 未来规划/示例，需人工确认】({len(ghost_plan)} 个)")
    if not ghost_plan:
        print("  （无）")
    for s, locs in sorted(ghost_plan.items()):
        print(f"\n  {s}:")
        for loc in locs[:5]:
            print(f"      - {loc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
