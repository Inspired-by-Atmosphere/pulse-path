#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一自检入口（零依赖）——CI 与本地共用。

顺序执行仓库内全部可离线验证的检查；任一失败则整体退出码 1。
新增带 --selftest 的脚本时，把它登记到 SELFTESTS 即可（或用 --discover 自动探测）。

用法:
    python scripts/run_checks.py            # 全部检查
    python scripts/run_checks.py --list     # 只列将执行的步骤
    python scripts/run_checks.py --discover # 自动把带 --selftest 的脚本也纳入
"""

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (显示名, 命令参数列表)；命令一律用 sys.executable 起，避免解释器不一致
STEPS = [
    ("单元测试 mem_guard", ["skills/memory-pointer-system/scripts/test_mem_guard.py"]),
    ("发布前敏感扫描", ["scripts/check_secrets.py", "."]),
    ("链完整性自检", ["skills/pulse-path/scripts/scan_links.py", "skills"]),
]

# 带 --selftest 的脚本（离线夹具，不联网、不需要任何 key）
SELFTESTS = [
    "skills/memory-pointer-system/scripts/scan_archived_deps.py",
    "skills/pulse-path/scripts/scan_link_graph.py",
    "skills/memory-pointer-system/scripts/stress_pointer_test.py",
    "skills/memory-governance/scripts/ssot_check.py",
    "skills/memory-pointer-system/scripts/project_registry.py",
]

# MCP 自测（不联网，spawn 本地子进程走 JSON-RPC）
MCP_TEST = "mcp_server/test_mcp.py"


def _env():
    e = dict(os.environ)
    # 检查必须离线确定：剥掉代理变量，避免脚本内 urllib 走隧道
    for k in list(e):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            e.pop(k, None)
    return e


def run(name, args):
    cmd = [sys.executable] + args
    print("\n=== %s ===\n$ %s" % (name, " ".join(args)))
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=_env(), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        print("TIMEOUT (600s)")
        return False, "timeout"
    tail = (p.stdout or "").strip().splitlines()
    for line in tail[-12:]:
        print("  " + line)
    if p.stderr.strip():
        print("  [stderr] " + p.stderr.strip().splitlines()[-1][:300])
    ok = p.returncode == 0
    print("  -> %s (exit %d)" % ("PASS" if ok else "FAIL", p.returncode))
    return ok, (p.stdout or "") + (p.stderr or "")


def main():
    ap = argparse.ArgumentParser(description="pulse-path 统一自检入口")
    ap.add_argument("--list", action="store_true", help="只列出步骤")
    ap.add_argument("--discover", action="store_true", help="自动纳入带 --selftest 的脚本")
    args = ap.parse_args()

    steps = list(STEPS)
    for rel in SELFTESTS:
        if os.path.exists(os.path.join(ROOT, rel)):
            steps.append(("%s --selftest" % os.path.basename(rel), [rel, "--selftest"]))
        else:
            print("[skip] 未找到（尚未实现）: %s" % rel)
    if os.path.exists(os.path.join(ROOT, MCP_TEST)):
        steps.append(("MCP 协议自测", [MCP_TEST]))

    if args.discover:
        for sub in ("skills", "scripts", "mcp_server"):
            base = os.path.join(ROOT, sub)
            for dirpath, _dirs, files in os.walk(base):
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, f), ROOT).replace(os.sep, "/")
                    if any(rel == s[1][0] for s in steps if len(s[1]) > 1):
                        continue
                    try:
                        txt = open(os.path.join(dirpath, f), encoding="utf-8", errors="ignore").read()
                    except OSError:
                        continue
                    if "--selftest" in txt and "selftest" not in rel:
                        steps.append(("%s --selftest" % f, [rel, "--selftest"]))

    print("pulse-path 自检 —— 共 %d 步" % len(steps))
    for i, (name, cmd) in enumerate(steps, 1):
        print("  %d) %-42s %s" % (i, name, " ".join(cmd)))
    if args.list:
        return 0

    results = []
    for name, cmd in steps:
        ok, _out = run(name, cmd)
        results.append((name, ok))

    print("\n" + "=" * 60)
    failed = [n for n, ok in results if not ok]
    for name, ok in results:
        print("  %-44s %s" % (name, "PASS" if ok else "FAIL"))
    print("=" * 60)
    print("结果: %d/%d 通过" % (len(results) - len(failed), len(results)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
