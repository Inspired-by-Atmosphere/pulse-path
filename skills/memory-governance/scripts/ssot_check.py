# -*- coding: utf-8 -*-
"""
ssot_check.py — 口径对账引擎（SSOT drift check）
=====================================================================
链表五律的"数据源改了，副本没改"体检：一份口径（端口/名字/版本/阈值）只有一处
权威源（SSOT），其余载体只能引用不能复制。改了权威源但旧值、旧说法还躺在记忆/
文档/技能里，读的人就会按错的干——这种漂移靠人眼抓不出来，本脚本抓。

判定口径（三级，避免误报）:
  🔴 明确错误：命中 rules[].forbid 里的禁用语，且该行没有任何合法语境词
     → 旧口径残留，修法工作单 fix_forbid（改成 expect，或删掉该副本）
  🟡 复核清单：命中 status_words（待批/进行中/未commit…）→ 状态词挂太久没人管
     → 只提示人工核一遍，不动文件（状态会自然过期，脚本不猜它该变成什么）
  📌 参考：rules[].expect / ssot 恒列出——是"改的时候照这个写"的对照表，不参与判定

合法语境豁免：行内出现任一 legal_context 词（弃用/历史/教训…）即判为"正当引用"。
这条专门防"文档里写着'旧端口 8080 已弃用'"被误报成漂移。

用法:
    python ssot_check.py                          # 人读报告
    python ssot_check.py --json                   # 机器可读（stdout 只有 JSON）
    python ssot_check.py --emit-workorders DIR    # 把 🔴 写成 markdown 工作单
    python ssot_check.py --root DOCS --root MEM   # 临时覆盖 scan_roots
    python ssot_check.py --rules-file RULES       # 换一份规则表
    python ssot_check.py --selftest               # 离线自测（临时夹具，不读本机数据）

环境变量:
    HERMES_HOME   记忆库根，默认 ~/.hermes；规则表里的相对路径按它解析
    SSOT_RULES    规则表路径，默认指针技能 scripts/mem_rules.json

规则来源: mem_rules.json 的 facts_ssot 段（声明式，引擎零业务 if）——
    {scan_roots:[...], rules:[{name, forbid:[...], expect, ssot}],
     legal_context:[...], status_words:[...]}
    填好的示例见 examples/facts_ssot.sample.json。

退出码: 恒 0（这是检测脚本，结论走 stdout；调度器才不会把"有预警"记成"脚本挂了"）
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime

try:                       # 跨平台：非 UTF-8 控制台/重定向下不因表情符号崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERMES_HOME = os.path.abspath(os.path.expanduser(
    os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes")))
os.environ["HERMES_HOME"] = HERMES_HOME   # 规范化后写回环境：规则表里的 ${HERMES_HOME} 才能正确展开

_BASE = os.path.dirname(os.path.abspath(__file__))
# 默认规则表与 mem_guard 同源（规则只有一份，两个引擎都读它）
DEFAULT_RULES_FILE = os.environ.get("SSOT_RULES") or os.path.normpath(
    os.path.join(_BASE, "..", "..", "memory-pointer-system", "scripts", "mem_rules.json"))

# 目录型扫描根默认看这些文本载体（口径会落在记忆/文档/脚本/配置里）
DEFAULT_EXTS = (".md", ".py", ".json", ".jsonl", ".yaml", ".yml", ".txt",
                ".bat", ".ps1", ".sh", ".toml", ".ini")
# 默认跳过的目录（版本库/依赖/缓存/构建产物/编辑器元数据）
DEFAULT_SKIP = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
                ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
                ".obsidian", ".idea", ".vscode", "site-packages", ".curator_backups"}
MAX_BYTES = 2_000_000      # 超过 2MB 视为数据文件，不扫（日志/转储）
TEXT_LIMIT = 160           # 报告里单行片段上限，防止把整行贴爆报表
# 纯 ASCII 的禁用语（端口号/版本号/模型名）走边界匹配，中文词做不到边界，走子串
_ASCII_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]+$")
_BOUNDARY_CACHE = {}

USAGE = """ssot_check — 口径对账（改了数据源，旧值/旧说法还残留在别处）

用法:
  python ssot_check.py [选项]

选项:
  --rules-file FILE      规则表（默认 $SSOT_RULES 或指针技能 scripts/mem_rules.json）
  --root PATH            临时覆盖 scan_roots，可重复（文件或目录）
  --json                 只输出 JSON（供 agent/管道消费）
  --emit-workorders DIR  把 🔴 明确错误写成 markdown 工作单（fix_forbid）到 DIR
  --limit N              人读报告里每个禁用语最多列几处（默认 6；JSON 不受限）
  --selftest             离线自测（临时夹具；不读也不写本机数据）
  --help                 本帮助

规则: mem_rules.json 的 facts_ssot 段（scan_roots / rules / legal_context / status_words）
分级: 🔴 禁用语残留（无合法语境） / 🟡 状态词待复核 / 📌 expect 参考
退出码: 恒 0（结论走 stdout）
"""


# ── 路径与配置 ────────────────────────────────────────────────────────
def resolve_path(p):
    """展开 ~/$VAR；相对路径按 HERMES_HOME 解析。空值返回 ''。"""
    if not p:
        return ""
    p = os.path.expandvars(os.path.expanduser(p))
    return p if os.path.isabs(p) else os.path.join(HERMES_HOME, p)


def load_rules(path):
    """读规则表。返回 (rules_dict, error_text)；读不到就返回空配置，不抛异常。"""
    try:
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}, "规则表顶层不是对象: %s" % path
        return data, ""
    except OSError as e:
        return {}, "规则表读取失败: %s (%s)" % (path, e)
    except ValueError as e:
        return {}, "规则表 JSON 无法解析: %s (%s)" % (path, e)


def display_path(fp, cwd=None):
    """报告里尽量用相对路径（换机器仍可读，也不把家目录写进报告）。"""
    cwd = cwd or os.getcwd()
    try:
        rel = os.path.relpath(fp, cwd)
    except ValueError:                     # Windows 跨盘符
        return fp
    return fp if rel.startswith("..") else rel.replace(os.sep, "/")


def match_token(line, word):
    """禁用语是否命中该行。ASCII 词加边界，防「8080」被「18080」截胡误报；
    中文词按词面子串（中文没有词边界，硬套 \\b 会漏报）。"""
    if _ASCII_TOKEN.match(word):
        pat = _BOUNDARY_CACHE.get(word)
        if pat is None:
            pat = re.compile(r"(?<![A-Za-z0-9_.:/-])%s(?![A-Za-z0-9_.:/-])" % re.escape(word))
            _BOUNDARY_CACHE[word] = pat
        return bool(pat.search(line))
    return word in line


# ── 扫描根展开 ────────────────────────────────────────────────────────
def collect_files(scan_roots, exts=DEFAULT_EXTS, max_bytes=MAX_BYTES):
    """把 scan_roots（文件/目录混排）展开成去重后的文件清单。

    返回 (roots_info, files, errors):
      roots_info 每个根的解析结果（原始写法 + 绝对路径 + 类型），缺失的根记 missing
      files      绝对路径列表（按 realpath 去重、按路径排序，结论可复现）
      errors     根缺失/读不到之类的软错误，只汇报不改结论等级
    """
    exts = tuple(e if e.startswith(".") else "." + e for e in exts)
    roots_info, files, errors = [], [], []
    seen = set()
    for raw in scan_roots or []:
        path = resolve_path(raw)
        info = {"spec": raw, "path": path, "kind": "missing", "files": 0}
        if path and os.path.isfile(path):
            info["kind"] = "file"
        elif path and os.path.isdir(path):
            info["kind"] = "dir"
        else:
            errors.append("扫描根不存在: %s" % (raw or "(空)"))
            roots_info.append(info)
            continue
        candidates = []
        if info["kind"] == "file":
            candidates.append(path)
        else:
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_SKIP)
                for fn in sorted(filenames):
                    if fn.lower().endswith(exts):
                        candidates.append(os.path.join(dirpath, fn))
        for fp in candidates:
            real = os.path.realpath(fp)
            if real in seen:
                continue
            seen.add(real)
            files.append(os.path.abspath(fp))
            info["files"] += 1
        roots_info.append(info)
    files.sort(key=lambda p: p.replace(os.sep, "/").lower())
    return roots_info, files, errors


# ── 核心：对账 ────────────────────────────────────────────────────────
def scan(cfg, exts=DEFAULT_EXTS, max_bytes=MAX_BYTES, display_cwd=None):
    """扫 scan_roots 里的文件，产出报告 dict（结构即 --json 输出）。

    cfg 是 mem_rules.json 的 facts_ssot 段。引擎只做四件事：
      展开根 → 逐行匹配禁用语 → 豁免过滤 → 分级记录。
    规则数据变了不用改这里。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    legal = [w for w in (cfg.get("legal_context") or []) if isinstance(w, str) and w]
    status_words = [w for w in (cfg.get("status_words") or []) if isinstance(w, str) and w]
    rules = [r for r in (cfg.get("rules") or []) if isinstance(r, dict)]

    roots_info, files, errors = collect_files(cfg.get("scan_roots"), exts, max_bytes)
    pairs = [(r, w) for r in rules for w in (r.get("forbid") or []) if isinstance(w, str) and w]

    red, yellow = [], []
    seen_red, seen_exempt = set(), set()
    exempt = 0
    files_scanned = 0

    for fp in files:
        try:
            if os.path.getsize(fp) > max_bytes:
                continue
        except OSError as e:
            errors.append("读取失败: %s (%s)" % (fp, e))
            continue
        try:
            with io.open(fp, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            errors.append("读取失败: %s (%s)" % (fp, e))
            continue
        files_scanned += 1
        shown = display_path(fp, display_cwd)
        for lineno, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            snippet = line.strip()
            if len(snippet) > TEXT_LIMIT:
                snippet = snippet[:TEXT_LIMIT - 3] + "..."
            has_ctx = any(w in line for w in legal)
            for rule, word in pairs:
                if not match_token(line, word):
                    continue
                key = (fp, lineno, word)
                if has_ctx:
                    if key not in seen_exempt:
                        seen_exempt.add(key)
                        exempt += 1
                    continue
                if key in seen_red:
                    continue
                seen_red.add(key)
                red.append({
                    "rule": rule.get("name") or "(未命名口径)",
                    "forbid": word,
                    "expect": rule.get("expect") or "",
                    "ssot": rule.get("ssot") or "",
                    "file": shown,
                    "line": lineno,
                    "text": snippet,
                    "fix": "fix_forbid",
                })
            if status_words:
                hit = [w for w in status_words if match_token(line, w)]
                if hit and not has_ctx:
                    yellow.append({
                        "words": hit,
                        "file": shown,
                        "line": lineno,
                        "text": snippet,
                    })

    red.sort(key=lambda x: (x["file"], x["line"], x["forbid"]))
    yellow.sort(key=lambda x: (x["file"], x["line"]))
    expect_refs = [{"name": r.get("name") or "(未命名口径)",
                    "expect": r.get("expect") or "",
                    "ssot": r.get("ssot") or ""}
                   for r in rules if (r.get("expect") or r.get("ssot"))]

    return {
        "rules_file": "",
        "scan_roots": roots_info,
        "legal_context": legal,
        "status_words": status_words,
        "rule_count": len(rules),
        "forbid_count": len(pairs),
        "files_scanned": files_scanned,
        "red_count": len(red),
        "yellow_count": len(yellow),
        "exempt_count": exempt,
        "red": red,
        "yellow": yellow,
        "expect_refs": expect_refs,
        "workorders": [],
        "errors": errors,
    }


# ── 人读报告 ──────────────────────────────────────────────────────────
def print_report(report, limit=6, out=None):
    out = out or sys.stdout
    say = lambda s="": print(s, file=out)
    roots = report["scan_roots"]
    exist = sum(1 for r in roots if r["kind"] != "missing")
    say("口径对账（SSOT）—— 规则表: %s" % (report["rules_file"] or "(未指定)"))
    say("配置: 口径 %d 条 / 禁用语 %d 个 / 合法语境词 %d 个 / 状态词 %d 个"
        % (report["rule_count"], report["forbid_count"],
           len(report["legal_context"]), len(report["status_words"])))
    say("扫描根: %d 个（存在 %d）/ 扫描文件: %d 个"
        % (len(roots), exist, report["files_scanned"]))
    for r in roots:
        flag = {"dir": "✅", "file": "📄"}.get(r["kind"], "❌")
        say("  %s %s  (%s, %d 文件)" % (flag, r["spec"], r["kind"], r["files"]))

    red = report["red"]
    say("")
    say("=== 🔴 明确错误 %d 处（旧口径残留 → fix_forbid）===" % len(red))
    if not red:
        say("  （无）")
    groups = {}
    for item in red:
        groups.setdefault((item["rule"], item["forbid"], item["expect"], item["ssot"]), []).append(item)
    for (name, forbid, expect, ssot), items in groups.items():
        tail = []
        if expect:
            tail.append("期望「%s」" % expect)
        if ssot:
            tail.append("权威源: %s" % ssot)
        say("")
        say("■ %s —— 禁用语「%s」%s" % (name, forbid, ("｜" + "｜".join(tail)) if tail else ""))
        for it in items[:limit]:
            say("   %s:%d" % (it["file"], it["line"]))
            say("      %s" % it["text"])
        if len(items) > limit:
            say("   ... 其余 %d 处（用 --json 取全量）" % (len(items) - limit))

    say("")
    say("=== 🟡 状态词待复核 %d 处（挂太久就回去核一遍）===" % report["yellow_count"])
    if not report["yellow"]:
        say("  （无）")
    for it in report["yellow"][:limit]:
        say("   %s:%d  [%s] %s" % (it["file"], it["line"], "/".join(it["words"]), it["text"]))
    if report["yellow_count"] > limit:
        say("   ... 其余 %d 处（用 --json 取全量）" % (report["yellow_count"] - limit))

    if report["expect_refs"]:
        say("")
        say("=== 📌 期望值参考（不参与判定，改的时候照这个写）===")
        for ref in report["expect_refs"]:
            say("   %s: %s   ← %s" % (ref["name"], ref["expect"] or "(未填)",
                                      ref["ssot"] or "(未标权威源)"))

    if report["errors"]:
        say("")
        say("=== 软错误 %d 处（不影响已有结论）===" % len(report["errors"]))
        for e in report["errors"][:5]:
            say("  " + e)
        if len(report["errors"]) > 5:
            say("  ... 其余 %d 处" % (len(report["errors"]) - 5))

    say("")
    if red or report["yellow_count"]:
        say("结论: 🔴 %d / 🟡 %d / 合法语境豁免 %d —— 先修 🔴，🟡 只复核"
            % (len(red), report["yellow_count"], report["exempt_count"]))
    else:
        say("✅ 未发现口径漂移（%d 个文件 / %d 个禁用语，豁免 %d 处正当引用）"
            % (report["files_scanned"], report["forbid_count"], report["exempt_count"]))


# ── 工作单落盘 ────────────────────────────────────────────────────────
def emit_workorders(report, outdir):
    """把 🔴 明确错误写成 markdown 工作单；返回写出的路径列表（无错误则空）。"""
    if not report["red"]:
        return []
    os.makedirs(outdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    path = os.path.join(outdir, "fix_forbid-%s.md" % stamp)
    lines = ["# 口径对账工作单 — fix_forbid", "",
             "- 生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
             "- 规则表: `%s`" % (report["rules_file"] or "(未指定)"),
             "- 明确错误: %d 处 / 涉及文件 %d 个"
             % (report["red_count"], len({i["file"] for i in report["red"]})),
             "",
             "> 每条都要处置：改成期望值，或确认该行是正当历史引用后补一个合法语境词",
             "> （如「已弃用 / 历史 / 教训」）再重跑，别直接删证据。", ""]
    groups = {}
    for item in report["red"]:
        groups.setdefault((item["rule"], item["forbid"], item["expect"], item["ssot"]), []).append(item)
    for idx, ((name, forbid, expect, ssot), items) in enumerate(groups.items(), 1):
        lines.append("## %d. %s — 禁用语「%s」" % (idx, name, forbid))
        lines.append("")
        if expect:
            lines.append("- 期望值: `%s`" % expect)
        if ssot:
            lines.append("- 权威源: `%s`" % ssot)
        lines.append("")
        for it in items:
            lines.append("- [ ] `%s:%d`" % (it["file"], it["line"]))
            lines.append("      > %s" % it["text"])
            if expect:
                lines.append("      修法: 把「%s」改成「%s」（核对权威源后）；"
                             "若属正当历史引用，补合法语境词豁免" % (forbid, expect))
            else:
                lines.append("      修法: 删掉这处副本改为引用权威源；"
                             "若属正当历史引用，补合法语境词豁免")
        lines.append("")
    lines.append("合计 %d 条" % report["red_count"])
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return [path]


def parse_args(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--rules-file", default=None)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--emit-workorders", default=None)
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--help", "-h", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.help:
        print(USAGE)
        return 0
    if args.selftest:
        return selftest()
    try:
        rules_file = resolve_path(args.rules_file or DEFAULT_RULES_FILE)
        rules, err = load_rules(rules_file)
        cfg = rules.get("facts_ssot") if isinstance(rules.get("facts_ssot"), dict) else {}
        if args.root:                     # 命令行临时覆盖扫描根（不改规则表）
            cfg = dict(cfg)
            cfg["scan_roots"] = args.root
        report = scan(cfg)
        report["rules_file"] = rules_file
        if err:
            report["errors"].insert(0, err)
        if args.emit_workorders:
            report["workorders"] = emit_workorders(report, resolve_path(args.emit_workorders))
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        print_report(report, args.limit)
        if args.emit_workorders:
            if report["workorders"]:
                print("\n工作单已写出: %s" % ", ".join(report["workorders"]))
            else:
                print("\n无 🔴 明确错误，未生成工作单")
        if not cfg.get("rules") or not cfg.get("scan_roots"):
            print("\n提示: facts_ssot 段还是空的（开源版故意不带任何项目口径）。")
            print("      填法见 examples/facts_ssot.sample.json，或用 --root 先试扫一遍。")
    except Exception as e:                # 检测脚本不因异常改变退出码语义
        print("对账失败: %s: %s" % (type(e).__name__, e))
    return 0


# ── 自测（临时夹具，零外网、零本机依赖）──────────────────────────────
def selftest():
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        print("  %s %s" % ("✅" if cond else "❌", name))

    sandbox = tempfile.mkdtemp(prefix="pp_ssot_")
    try:
        def put(rel, text):
            p = os.path.join(sandbox, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with io.open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return p

        # 夹具：两个扫描根（一个目录 + 一个文件）+ 一个重复列出 + 一个缺失 + 一个被跳过目录
        put("docs/a.md",
            "端口写的是 8080。\n"                 # 1) 命中禁用语 → 🔴
            "8080 已弃用，改 18080。\n"           # 2) 合法语境（弃用）→ 豁免
            "旧名称只是历史记录。\n"              # 3) 合法语境（历史）→ 豁免
            "任务 进行中，等评审。\n")            # 4) 状态词 → 🟡
        put("docs/b.md",
            "全部正常，没有旧说法。\n"
            "现在端口统一 18080（旧值已在权威源改掉）。\n")   # 18080 含 8080 子串 → 不该误报
        put("docs/node_modules/dep.md", "8080\n")     # 默认跳过目录
        put("notes/x.md",
            "旧名称 还留在旧交付物里\n"           # 🔴（第二根、第二条规则）
            "待批 的事项挂着\n")                  # 🟡
        rules_path = os.path.join(sandbox, "rules.json")
        rules = {"facts_ssot": {
            "scan_roots": [os.path.join(sandbox, "docs"),       # 目录
                           os.path.join(sandbox, "notes", "x.md"),  # 单文件
                           os.path.join(sandbox, "docs", "a.md"),   # 与目录重叠 → 去重
                           os.path.join(sandbox, "missing.md")],    # 缺失 → 软错误
            "rules": [
                {"name": "部署端口", "forbid": ["8080"], "expect": "18080", "ssot": "config.yaml"},
                {"name": "产品命名", "forbid": ["旧名称"], "expect": "新名称", "ssot": "docs/naming.md"},
            ],
            "legal_context": ["弃用", "历史", "教训"],
            "status_words": ["进行中", "待批"],
        }}
        with io.open(rules_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)

        report = scan(rules["facts_ssot"], display_cwd=sandbox)

        check("扫描根解析: 2 存在 + 1 缺失",
              [r["kind"] for r in report["scan_roots"]] == ["dir", "file", "file", "missing"])
        check("重叠扫描根去重（目录 + 同目录内文件只算一次）", report["files_scanned"] == 3)
        check("缺失扫描根只记软错误、不中断", len(report["errors"]) == 1
              and "missing.md" in report["errors"][0])
        check("允许语境豁免的默认跳过目录（node_modules）", all(
            "node_modules" not in it["file"] for it in report["red"]))

        red = report["red"]
        check("🔴 命中禁用语 = 2 处（每处副本各一条）", report["red_count"] == 2)
        check("🔴 首条定位到 docs/a.md:1", [it["file"] for it in red] == ["docs/a.md", "notes/x.md"]
              and [it["line"] for it in red] == [1, 1])
        check("🔴 带出规则名/期望值/权威源", all(it["rule"] and it["expect"] and it["ssot"]
                                                and it["fix"] == "fix_forbid" for it in red))
        check("禁用语按词面命中（8080 不是 18080 的子串误报）",
              [it["forbid"] for it in red] == ["8080", "旧名称"])
        check("ASCII 词边界：docs/b.md 的 18080 不误报",
              all(it["file"] != "docs/b.md" for it in red))

        check("合法语境豁免: 弃用/历史 两行不报", report["exempt_count"] == 2)
        check("🟡 状态词命中 2 行", report["yellow_count"] == 2)
        check("🟡 记录命中词", [it["words"] for it in report["yellow"]] == [["进行中"], ["待批"]])
        check("📌 expect 参考恒列出（不参与判定）", len(report["expect_refs"]) == 2
              and report["expect_refs"][0]["ssot"] == "config.yaml")

        # 关闭合法语境：豁免消失，同样两行变成 🔴（证明豁免确实来自规则表而非硬编码）
        cfg2 = dict(rules["facts_ssot"])
        cfg2["legal_context"] = []
        r2 = scan(cfg2, display_cwd=sandbox)
        check("legal_context 为空时豁免归零、命中数上升",
              r2["exempt_count"] == 0 and r2["red_count"] == 4)

        # 给"合法语境"换成别的词：只有 教训 生效
        cfg3 = dict(rules["facts_ssot"])
        cfg3["legal_context"] = ["教训"]
        r3 = scan(cfg3, display_cwd=sandbox)
        check("合法语境词可被规则表整体替换", r3["exempt_count"] == 0 and r3["red_count"] == 4)

        # 空配置：不报错、不虚构问题
        empty = scan({}, display_cwd=sandbox)
        check("空 facts_ssot 段返回空结论", empty["red_count"] == 0 and empty["files_scanned"] == 0
              and empty["errors"] == [])

        # 工作单
        wo_dir = os.path.join(sandbox, "wo")
        wos = emit_workorders(report, wo_dir)
        check("--emit-workorders 落盘 1 份工作单", len(wos) == 1 and os.path.isfile(wos[0]))
        wo_text = io.open(wos[0], encoding="utf-8").read() if wos else ""
        check("工作单含定位 + 修法 + 期望值",
              "docs/a.md:1" in wo_text and "修法" in wo_text and "18080" in wo_text)
        check("无 🔴 时不产工作单", emit_workorders(empty, os.path.join(sandbox, "wo2")) == []
              and not os.path.isdir(os.path.join(sandbox, "wo2")))

        # 端到端 CLI：退出码恒 0 + JSON 可解析 + 结构稳定 + 相对路径
        import subprocess
        script = os.path.abspath(__file__)
        proc = subprocess.run([sys.executable, script, "--rules-file", rules_path, "--json"],
                              cwd=sandbox, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        check("CLI --json 退出码为 0（有 🔴 也是 0）", proc.returncode == 0)
        try:
            payload = json.loads(proc.stdout)
        except Exception:
            payload = None
        check("CLI --json 输出可解析", isinstance(payload, dict))
        check("CLI JSON 结构与 scan() 一致",
              isinstance(payload, dict) and set(payload) == set(report))
        check("报告路径为相对路径（不泄露绝对路径）",
              all(not os.path.isabs(it["file"]) for it in (payload or {}).get("red", [])))

        proc2 = subprocess.run([sys.executable, script, "--rules-file", rules_path],
                               cwd=sandbox, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
        check("人读报告退出码为 0 且含 🔴/🟡 分区",
              proc2.returncode == 0 and "🔴" in proc2.stdout and "🟡" in proc2.stdout)

        proc3 = subprocess.run([sys.executable, script, "--rules-file", rules_path,
                                "--root", os.path.join(sandbox, "docs"), "--json"],
                               cwd=sandbox, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
        try:
            payload3 = json.loads(proc3.stdout)
        except Exception:
            payload3 = {}
        check("--root 覆盖扫描根生效（只看 docs 时只剩 1 处 🔴）",
              proc3.returncode == 0 and payload3.get("red_count") == 1)

        proc4 = subprocess.run([sys.executable, script, "--rules-file",
                                os.path.join(sandbox, "nope.json")],
                               cwd=sandbox, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120)
        check("规则表缺失时 CLI 仍退出 0", proc4.returncode == 0)
    finally:
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)

    ok = sum(1 for _, c in checks if c)
    print("\nSELFTEST %s %d/%d" % ("OK" if ok == len(checks) else "FAIL", ok, len(checks)))
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
