# -*- coding: utf-8 -*-
"""
scan_link_graph.py — 文档链图体检（把"断链检测"升级为整张链图）
=====================================================================
`scan_links.py` 只回答"哪些引用悬空了"，本脚本回答更完整的问题：这张知识网到底
长什么样——谁指向谁（backlinks）、谁一个入链都没有（orphans）、哪几个节点是枢纽
（top-degree）、哪些链接指向了不存在的目标（ghosts）。

抽取三类引用（均在代码围栏外；`[[wikilink]]` 另排除行内代码，防示例被当真）：
  1. `[[wikilink]]` / `![[embed]]`：按 Obsidian 口径解析——笔记名、目录/笔记名、
     带 `#标题` 片段、带 `|别名` 都解析到节点
  2. 相对 md 链接：`[文字](相对路径.md)`（含 `#锚点`、`%20` 转义、`./` `../`）
  3. 路径引用：正文/行内代码里的 `dir/file.ext`（文档、脚本、配置都算）——
     目标存在记为外部引用，不存在记为 ghost，用于抓"文档写了但文件没了"

节点 = 扫描目录内的 .md 文档（id 为相对所属目录的 posix 路径）。
孤立 = 0 入链（按链表律：新节点必须至少有一条入链，孤岛就是没挂上链的节点）。

用法:
    python scan_link_graph.py                    # 扫当前目录
    python scan_link_graph.py docs skills        # 扫多个目录（同一张图）
    python scan_link_graph.py docs --limit 50    # 列表长度上限（默认 20）
    python scan_link_graph.py docs --json        # 机器可读全量
    python scan_link_graph.py --selftest         # 离线自测（临时夹具）

退出码: 0（体检报告；结论走 stdout，便于定时任务收集）
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from urllib.parse import unquote

try:                       # 跨平台：非 UTF-8 控制台/重定向下不因表情符号崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SKIP_DIRS = {".git", ".hg", ".svn", ".obsidian", ".trash", "node_modules", "__pycache__",
             ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
             ".idea", ".vscode", "site-packages", ".curator_backups"}

# `[[目标#片段|别名]]`；`![[嵌入]]` 同解析，仅多一个 !
WIKI_RE = re.compile(r"!?\[\[([^\[\]\n]+?)\]\]")
# `[文字](目标 "标题")`；图片 `![alt](src)` 单独按路径引用处理
MD_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*<?([^)>\s]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
IMG_LINK_RE = re.compile(r"!\[[^\]\n]*\]\(\s*<?([^)>\s]+)>?[^)]*\)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# 路径引用：至少一级目录 + 已知扩展名；前后不许紧贴文件名字符（排除 URL 与盘符路径）
PATH_REF_RE = re.compile(
    r"(?<![\w./:\\-])((?:\.{0,2}/)?(?:[\w\u4e00-\u9fff.@-]+/)+[\w\u4e00-\u9fff.@-]+\.[A-Za-z0-9]{1,8})(?![\w/])")
REF_EXTS = {".md", ".markdown", ".py", ".js", ".ts", ".json", ".jsonl", ".yaml", ".yml",
            ".toml", ".ini", ".cfg", ".txt", ".csv", ".sh", ".bat", ".ps1", ".html",
            ".css", ".png", ".jpg", ".jpeg", ".svg", ".pdf"}
PLACEHOLDERS = ("path/to", "your/", "<", ">", "${", "$(", "{", "}", "*", "...", "…")
NESTED_DEPTH = 2           # 集合根兜底：pulse-path/SKILL.md 实指 skills/pulse-path/SKILL.md
NESTED_CAP = 400           # 兜底目录数量上限（大库不至于退化成全盘搜索）
# 环境变量占位路径（`HERMES_HOME/memories/MEMORY.md` / `$VAR/x` / `%VAR%\x`）不是幽灵链接
ENV_HEAD_RE = re.compile(r"^(?:\$|%|[A-Z][A-Z0-9_]{2,}/)")

USAGE = """scan_link_graph — 文档链图体检（节点/链接/幽灵/孤立/连接度/反向索引）

用法:
  python scan_link_graph.py [目录...] [选项]

选项:
  --json        输出 JSON（全量链接/幽灵；top_degree 仍受 --limit 限制）
  --limit N     列表长度上限（人读报告的连接度/孤立/幽灵/反向索引，默认 20）
  --selftest    离线自测（临时夹具；不读也不写本机数据）
  --help        本帮助

抽取: [[wikilink]]（含 |别名 / #片段 / 嵌入）、相对 md 链接、正文与行内代码里的路径引用。
节点: 目录内 .md；孤立 = 0 入链（孤岛节点 = 没挂上链的知识）。
退出码: 0（结论走 stdout）
"""


# ── 文本预处理 ────────────────────────────────────────────────────────
def strip_fences(text):
    """把 ``` / ~~~ 围栏代码块清空（保留换行数以对齐行号）。"""
    out, fence = [], None
    for line in text.split("\n"):
        s = line.lstrip()
        if fence is None and (s.startswith("```") or s.startswith("~~~")):
            fence = s[:3]
            out.append("")
            continue
        if fence is not None:
            if s.startswith(fence):
                fence = None
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)


def strip_inline_code(text):
    """把行内 `code` 清空（保留行数），防代码示例里的 [[x]] 被当作链接。"""
    return INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), text)


def mask_spans(text, regex):
    """把已单独处理的语法（如 md 链接目标）替换成等长空白，避免同一引用被重复计数。"""
    return regex.sub(lambda m: " " * len(m.group(0)), text)


def _line_of(text, pos):
    return text.count("\n", 0, pos) + 1


# ── 节点收集 ──────────────────────────────────────────────────────────
def collect_nodes(dirs):
    """扫所有目录的 .md，返回节点列表 [{id, abs, root, rel}]（按 id 排序、去重）。

    id 为相对所属目录的 posix 路径；同 id 冲突时两方都加目录名前缀，保证唯一。
    """
    nodes, seen = [], set()
    roots = [os.path.abspath(d) for d in dirs]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                if not fn.lower().endswith(".md"):
                    continue
                fp = os.path.abspath(os.path.join(dirpath, fn))
                key = os.path.realpath(fp)
                if key in seen:
                    continue
                seen.add(key)
                nodes.append({"abs": fp, "root": root,
                              "rel": os.path.relpath(fp, root).replace(os.sep, "/")})
    # 目录嵌套时同一文件可能被两个根扫到 → 已在上面按 realpath 去重；取最先命中的根
    counter = Counter(n["rel"] for n in nodes)
    for n in nodes:
        if counter[n["rel"]] > 1:
            n["id"] = os.path.basename(n["root"]) + "/" + n["rel"]
        else:
            n["id"] = n["rel"]
    nodes.sort(key=lambda n: n["id"])
    return nodes


def build_index(nodes):
    """建解析索引：绝对路径 / 相对路径（去扩展名）/ 路径后缀 / 文件名。"""
    by_abs, by_rel, by_suffix, by_base = {}, {}, defaultdict(list), defaultdict(list)
    for n in nodes:
        by_abs[os.path.realpath(n["abs"])] = n
        rel_noext = n["rel"][:-3] if n["rel"].lower().endswith(".md") else n["rel"]
        by_rel[rel_noext] = n
        parts = rel_noext.split("/")
        for i in range(len(parts)):
            by_suffix["/".join(parts[i:])].append(n)
        by_base[parts[-1]].append(n)
    return by_abs, by_rel, by_suffix, by_base


def _pick(cands, src):
    """歧义消解：同目录优先 → 路径最短 → 字典序（保证结果确定）。"""
    if not cands:
        return None
    src_dir = os.path.dirname(src["rel"])

    def rank(n):
        d = os.path.dirname(n["rel"])
        return (0 if d == src_dir else 1, n["rel"].count("/"), n["rel"])

    return sorted(cands, key=rank)[0]


def resolve_wiki(target, src, index):
    """解析 [[...]] 目标。返回 (node 或 None, 归一化目标)。"""
    t = target.split("|")[0].split("#")[0].strip()
    t = t.replace("\\", "/").strip("/")
    if not t:                                   # [[#本地标题]] = 自引用，不计边
        return None, ""
    t_noext = t[:-3] if t.lower().endswith(".md") else t
    _by_abs, by_rel, by_suffix, by_base = index
    if t_noext in by_rel:
        return by_rel[t_noext], t
    # 相对当前文件目录：[[../a]] / [[sub/b]]
    cand = os.path.normpath(os.path.join(os.path.dirname(src["rel"]), t_noext)).replace(os.sep, "/")
    if cand in by_rel:
        return by_rel[cand], t
    if t_noext in by_suffix:
        return _pick(by_suffix[t_noext], src), t
    if t_noext.split("/")[-1] in by_base:
        return _pick(by_base[t_noext.split("/")[-1]], src), t
    return None, t


def resolve_path(raw, src, roots, by_abs, nested=()):
    """解析相对/根相对路径。返回 (node 或 None, 绝对目标或 None, 归一化目标)。

    解析顺序：源文件目录 → 各扫描根（库根写法）→ 根下 1~2 层子目录（集合根写法，
    如文档写 `pulse-path/SKILL.md` 实指 `skills/pulse-path/SKILL.md`）。
    """
    raw = unquote(raw.split("#")[0].split("?")[0]).strip()
    if not raw or raw.startswith(("http://", "https://", "mailto:", "tel:", "//")):
        return None, None, ""
    raw = raw.replace("\\", "/")
    if os.path.splitext(raw)[1].lower() not in REF_EXTS:
        return None, None, ""
    if any(p in raw for p in PLACEHOLDERS):
        return None, None, ""
    if ENV_HEAD_RE.search(raw):                  # 环境变量占位，不是文档引用
        return None, None, ""
    # `MEMORY.md/USER.md` 这类是并列文件名，不是路径：非末段带已知扩展名 → 跳过
    for seg in raw.split("/")[:-1]:
        if os.path.splitext(seg)[1].lower() in REF_EXTS:
            return None, None, ""
    tries = []
    if raw.startswith("/"):                      # 库根相对
        tries += [os.path.join(r, raw.lstrip("/")) for r in roots]
    else:
        tries.append(os.path.join(os.path.dirname(src["abs"]), raw))
        tries += [os.path.join(r, raw) for r in roots]
        tries += [os.path.join(d, raw) for d in nested]
    for t in tries:
        t = os.path.abspath(t)
        if os.path.isfile(t):
            node = by_abs.get(os.path.realpath(t))
            return node, t, raw
    return None, None, raw


def collect_nested_dirs(roots, depth=NESTED_DEPTH, cap=NESTED_CAP):
    """收集根下 1~depth 层子目录，作为"集合根"写法的解析兜底。"""
    out = []
    for root in roots:
        for dirpath, dirnames, _files in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            rel = os.path.relpath(dirpath, root)
            if rel == ".":
                continue
            if rel.count(os.sep) + 1 >= depth:
                dirnames[:] = []
            out.append(dirpath)
            if len(out) >= cap:
                return out[:cap]
    return out


# ── 链图构建 ──────────────────────────────────────────────────────────
def build_graph(dirs, limit=20):
    """扫目录建链图。返回报告 dict（即 --json 结构）。"""
    roots = [os.path.abspath(d) for d in dirs]
    nodes = collect_nodes(roots)
    index = build_index(nodes)
    by_abs = index[0]
    nested = collect_nested_dirs([r for r in roots if os.path.isdir(r)])

    links, ghosts, externals = [], [], []
    backlinks = defaultdict(list)
    out_deg = Counter()
    in_deg = Counter()

    for src in nodes:
        try:
            text = io.open(src["abs"], encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        body = strip_fences(text)
        linkable = strip_inline_code(body)

        seen_edges = set()
        for m in WIKI_RE.finditer(linkable):
            node, target = resolve_wiki(m.group(1), src, index)
            if not target:
                continue
            if node is None:
                ghosts.append({"source": src["id"], "target": target, "kind": "wiki",
                               "line": _line_of(linkable, m.start())})
            else:
                key = (node["id"], "wiki")
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                links.append({"source": src["id"], "target": node["id"], "kind": "wiki"})

        for m in MD_LINK_RE.finditer(linkable):
            raw = m.group(1).strip()
            if not raw.lower().split("#")[0].endswith((".md", ".markdown")):
                continue
            node, abs_target, target = resolve_path(raw, src, roots, by_abs, nested)
            if not target:
                continue
            if node is None:
                ghosts.append({"source": src["id"], "target": target, "kind": "md",
                               "line": _line_of(linkable, m.start())})
            elif (node["id"], "md") not in seen_edges:
                seen_edges.add((node["id"], "md"))
                links.append({"source": src["id"], "target": node["id"], "kind": "md"})

        # 路径引用：正文 + 行内代码 + 图片地址（md/图片链接已单独处理，先遮掉防重复计数）
        # 注意用 body 而非 linkable：行内代码里的路径是引用主体，不能先被清掉
        path_text = mask_spans(mask_spans(body, MD_LINK_RE), IMG_LINK_RE)
        for m in PATH_REF_RE.finditer(path_text):
            raw = m.group(1).rstrip(".,;:)]}'\"")
            node, abs_target, target = resolve_path(raw, src, roots, by_abs, nested)
            if not target:
                continue
            if node is not None:
                if (node["id"], "path") in seen_edges:
                    continue
                seen_edges.add((node["id"], "path"))
                links.append({"source": src["id"], "target": node["id"], "kind": "path"})
            elif abs_target is not None:
                externals.append({"source": src["id"], "target": abs_target,
                                  "line": _line_of(body, m.start())})
            else:
                ghosts.append({"source": src["id"], "target": target, "kind": "path",
                               "line": _line_of(body, m.start())})
        for m in IMG_LINK_RE.finditer(linkable):
            raw = m.group(1).strip()
            if raw.startswith(("http://", "https://", "data:")):
                continue
            node, abs_target, target = resolve_path(raw, src, roots, by_abs, nested)
            if target and abs_target is not None and node is None:
                externals.append({"source": src["id"], "target": abs_target,
                                  "line": _line_of(linkable, m.start())})
            elif target and abs_target is None:
                ghosts.append({"source": src["id"], "target": target, "kind": "image",
                               "line": _line_of(linkable, m.start())})

    for l in links:
        out_deg[l["source"]] += 1
        in_deg[l["target"]] += 1
        backlinks[l["target"]].append({"source": l["source"], "kind": l["kind"]})

    orphans = sorted(n["id"] for n in nodes if in_deg[n["id"]] == 0)
    isolated = sorted(n["id"] for n in nodes if in_deg[n["id"]] == 0 and out_deg[n["id"]] == 0)
    degree = {n["id"]: {"id": n["id"], "in": in_deg[n["id"]], "out": out_deg[n["id"]],
                        "degree": in_deg[n["id"]] + out_deg[n["id"]]} for n in nodes}
    top = sorted(degree.values(), key=lambda d: (-d["degree"], -d["in"], d["id"]))[:limit]

    ghost_targets = Counter(g["target"] for g in ghosts)
    report = {
        "dirs": [r for r in roots if os.path.isdir(r)],
        "stats": {
            "nodes": len(nodes),
            "links": len(links),
            "links_by_kind": dict(Counter(l["kind"] for l in links)),
            "ghosts": len(ghosts),
            "ghost_targets": len(ghost_targets),
            "orphans": len(orphans),
            "isolated": len(isolated),
            "externals": len(externals),
        },
        "nodes": [{"id": n["id"], "rel": n["rel"], "root": n["root"]} for n in nodes],
        "links": sorted(links, key=lambda l: (l["source"], l["target"], l["kind"])),
        "ghosts": sorted(ghosts, key=lambda g: (g["source"], g["line"], g["target"])),
        "orphans": orphans,
        "top_degree": top,
        "backlinks": {t: sorted(v, key=lambda x: (x["source"], x["kind"]))
                      for t, v in sorted(backlinks.items())},
        "externals": sorted(externals, key=lambda e: (e["source"], e["line"])),
    }
    return report


# ── 人读报告 ──────────────────────────────────────────────────────────
def print_report(report, limit=20):
    s = report["stats"]
    print("扫描目录: %s" % (", ".join(report["dirs"]) or "(无可用目录)"))
    print("节点(.md): %d   链接: %d（%s）" % (
        s["nodes"], s["links"],
        " / ".join("%s %d" % (k, v) for k, v in sorted(s["links_by_kind"].items())) or "无"))
    print("幽灵链接: %d 处（去重 %d 个目标）" % (s["ghosts"], s["ghost_targets"]))
    print("孤立节点(0 入链): %d   全孤立(0 入/0 出): %d   外部路径引用: %d"
          % (s["orphans"], s["isolated"], s["externals"]))

    print("\n=== 连接度 Top %d ===" % min(limit, len(report["top_degree"])))
    if not report["top_degree"]:
        print("  （无节点）")
    for d in report["top_degree"]:
        print("  入%3d 出%3d 计%3d  %s" % (d["in"], d["out"], d["degree"], d["id"]))

    print("\n=== 孤立节点（无入链，前 %d 个）===" % limit)
    if not report["orphans"]:
        print("  （无——每个节点都至少被引用一次）")
    for nid in report["orphans"][:limit]:
        print("  " + nid)
    if len(report["orphans"]) > limit:
        print("  ... 其余 %d 个（用 --json 取全量）" % (len(report["orphans"]) - limit))

    print("\n=== 幽灵链接（按目标聚合，前 %d 个）===" % limit)
    if not report["ghosts"]:
        print("  （无——所有链接都能落地）")
    agg = defaultdict(list)
    for g in report["ghosts"]:
        agg[g["target"]].append(g)
    for target, items in sorted(agg.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]:
        kinds = ",".join(sorted({i["kind"] for i in items}))
        srcs = ", ".join("%s:%d" % (i["source"], i["line"]) for i in items[:3])
        print("  %d× [%s] (%s)  ← %s%s" % (
            len(items), target, kinds, srcs, " ..." if len(items) > 3 else ""))
    if len(agg) > limit:
        print("  ... 其余 %d 个目标" % (len(agg) - limit))

    print("\n=== 反向索引（入链最多，前 %d 个）===" % limit)
    ranked = sorted(report["backlinks"].items(),
                    key=lambda kv: (-len(kv[1]), kv[0]))[:limit]
    if not ranked:
        print("  （无入链）")
    for target, refs in ranked:
        srcs = ", ".join("%s(%s)" % (r["source"], r["kind"]) for r in refs[:4])
        print("  %s  ← %s%s" % (target, srcs, " ..." if len(refs) > 4 else ""))


def parse_args(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("dirs", nargs="*", default=[])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
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
        dirs = args.dirs or ["."]
        missing = [d for d in dirs if not os.path.isdir(d)]
        for d in missing:
            print("跳过（不存在或不是目录）: %s" % d)
        report = build_graph([d for d in dirs if os.path.isdir(d)], max(1, args.limit))
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_report(report, max(1, args.limit))
    except Exception as e:
        print("扫描失败: %s: %s" % (type(e).__name__, e))
    return 0


# ── 自测（临时夹具，零外网、零本机依赖）──────────────────────────────
def selftest():
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        print("  %s %s" % ("✅" if cond else "❌", name))

    sandbox = tempfile.mkdtemp(prefix="pp_link_graph_")
    try:
        v = os.path.join(sandbox, "vault")

        def put(rel, text):
            p = os.path.join(v, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with io.open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return p

        put("a.md", "# A\n[[b]]\n[to c](c.md)\n")
        put("b.md", "# B\n[[a|别名]]\n")
        put("c.md", "# C\n[[ghost-note]]\n")
        put("d.md", "# D\n没有链接的孤岛\n")
        put("sub/e.md", "# E\n[[a]]\n[up](../a.md)\n")
        put("code.md",
            "# Code\n```\n[[in-fence]]\n```\n"
            "`path/to/sample.md`\n`../missing/thing.md`\n")
        put("img.md", "![logo](pic/missing.png)\n![web](https://example.com/x.png)\n")
        put("node_modules/x.md", "[[a]]\n")     # 跳过目录
        put("empty/README.MD", "[[a]]\n")       # 大小写扩展名也算 md

        g = build_graph([v])
        stats = g["stats"]
        ids = [n["id"] for n in g["nodes"]]
        edges = {(l["source"], l["target"], l["kind"]) for l in g["links"]}
        ghost_targets = {x["target"] for x in g["ghosts"]}

        check("节点收集（含子目录/大小写扩展名，跳过 node_modules）",
              ids == ["a.md", "b.md", "c.md", "code.md", "d.md", "empty/README.MD",
                      "img.md", "sub/e.md"])
        check("wikilink 解析（[[b]]）", ("a.md", "b.md", "wiki") in edges)
        check("wikilink 带别名（[[a|别名]]）", ("b.md", "a.md", "wiki") in edges)
        check("md 相对链接（[to c](c.md)）", ("a.md", "c.md", "md") in edges)
        check("子目录 ../ 相对链接解析到节点", ("sub/e.md", "a.md", "md") in edges)
        check("子目录 [[a]] 解析到根节点", ("sub/e.md", "a.md", "wiki") in edges)
        check("幽灵 wikilink 被记录", "ghost-note" in ghost_targets)
        check("代码围栏内的 [[...]] 不当链接", "in-fence" not in ghost_targets)
        check("占位符路径（path/to/sample.md）不误报幽灵",
              "path/to/sample.md" not in ghost_targets)
        check("正文路径引用缺失 → 幽灵",
              any(x["target"] == "../missing/thing.md" and x["kind"] == "path"
                  for x in g["ghosts"]))
        check("图片缺失 → 幽灵(kind=image，且不重复计一次 path)",
              [x["kind"] for x in g["ghosts"] if x["target"] == "pic/missing.png"] == ["image"])
        check("md 链接不重复计成 path 引用",
              ("sub/e.md", "a.md", "path") not in edges
              and sum(1 for e in edges if e[0] == "sub/e.md") == 2)
        check("网络图片不算幽灵", not any("example.com" in t for t in ghost_targets))
        check("孤立节点 = 0 入链（d/code/img 与只出不进的 e、README）",
              g["orphans"] == ["code.md", "d.md", "empty/README.MD", "img.md", "sub/e.md"])
        check("全孤立(0 入/0 出) = code.md / d.md / img.md",
              stats["isolated"] == 3 and set(g["orphans"][1:4] + [g["orphans"][0]]) >=
              {"code.md", "d.md", "img.md"})
        check("连接度第一是 a.md（被 4 处引用、指向 b/c）",
              g["top_degree"][0]["id"] == "a.md" and g["top_degree"][0]["in"] == 4)
        check("反向索引记录入链来源与类型",
              {(r["source"], r["kind"]) for r in g["backlinks"]["a.md"]}
              == {("b.md", "wiki"), ("sub/e.md", "wiki"), ("sub/e.md", "md"),
                  ("empty/README.MD", "wiki")})
        check("链接计数与 kind 明细一致",
              stats["links"] == len(g["links"])
              and sum(stats["links_by_kind"].values()) == stats["links"])

        # 集合根写法 + 环境变量占位（单独夹具，避免影响上面的度数断言）
        v2 = os.path.join(sandbox, "vault2")

        def put2(rel, text):
            p = os.path.join(v2, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with io.open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return p

        put2("docs/refs.md", "`pulse-demo/SKILL.md`\n`HERMES_HOME/memories/MEMORY.md`\n")
        put2("skills/pulse-demo/SKILL.md", "# demo\n")
        g3 = build_graph([v2])
        check("集合根写法可解析（pulse-demo/SKILL.md → skills/pulse-demo/SKILL.md）",
              ("docs/refs.md", "skills/pulse-demo/SKILL.md", "path")
              in {(l["source"], l["target"], l["kind"]) for l in g3["links"]})
        check("环境变量占位路径（HERMES_HOME/...）不算幽灵",
              [x for x in g3["ghosts"] if x["target"].startswith("HERMES_HOME")] == [])
        check("并列文件名（MEMORY.md/USER.md）不算幽灵",
              not any("MEMORY.md/USER.md" in x["target"] for x in g3["ghosts"]))

        # --limit 只影响人读列表长度，不影响 JSON 全量
        g2 = build_graph([v], limit=2)
        check("--limit 截断连接度榜", len(g2["top_degree"]) == 2)

        # 端到端：子进程跑 --json
        import subprocess
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), v, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        check("CLI --json 退出码为 0", proc.returncode == 0)
        try:
            payload = json.loads(proc.stdout)
        except Exception:
            payload = None
        check("CLI --json 输出可解析", isinstance(payload, dict))
        check("CLI JSON 与 build_graph() 结构一致",
              isinstance(payload, dict) and set(payload) == set(g)
              and payload["stats"] == g["stats"])
        check("CLI JSON 节点路径为相对路径",
              all(not os.path.isabs(n["rel"]) for n in (payload or {}).get("nodes", [])))

        # 空目录：不崩、给出空图
        empty_dir = os.path.join(sandbox, "emptydir")
        os.makedirs(empty_dir, exist_ok=True)
        ge = build_graph([empty_dir])
        check("空目录返回空图", ge["stats"]["nodes"] == 0 and ge["links"] == [])
    finally:
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)

    ok = sum(1 for _, c in checks if c)
    print("\nSELFTEST %s %d/%d" % ("OK" if ok == len(checks) else "FAIL", ok, len(checks)))
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
