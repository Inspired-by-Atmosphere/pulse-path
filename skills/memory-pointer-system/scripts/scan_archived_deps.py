# -*- coding: utf-8 -*-
"""
scan_archived_deps.py — 归档依赖扫描（被归档/删除的技能，还有谁在引用）
=====================================================================
链表五律第 1 条「删除 = 重接链」的自动化体检：技能一旦被移进归档区（.archive）
或直接删掉，散落在记忆/文档/脚本/配置里的老引用就变成了悬空指针。本脚本把归档区
当"已死节点清单"，反向扫描所有活文件，输出每个归档技能仍被哪些 `path:line` 引用，
据此决定：重接链（改引用方）还是确认可安全留在归档。

判定口径（避免误报/漏报）:
  - 目标名 = 归档区下的目录名 + 其中 SKILL.md 的 `name:` 字段（别名一并参与匹配）
  - 按词边界匹配：`old-skill` 不会命中 `old-skill-extra` / `my-old-skill` / `old_skill_x`
  - 命中行若同时指向归档区路径（如 `skills/.archive/old-skill`），判为"归档提及"
    ——那是在说明"它已归档"，不是活依赖，单独计数、不计入引用方
  - 二进制/超大文件跳过；归档区自身与 .git/node_modules 等目录默认跳过
  - 只统计能读成文本的文件，读失败计入 errors 不影响整体结论

用法:
    python scan_archived_deps.py                     # 默认 $HERMES_HOME/skills/.archive
    python scan_archived_deps.py --json              # 机器可读（stdout 只有 JSON）
    python scan_archived_deps.py --archive-dir ./skills/.archive \\
        --search-root ./skills --search-root ./docs --search-root ./scripts
    python scan_archived_deps.py --ext .md --ext .py      # 只查这两类文件
    python scan_archived_deps.py --skip-dir 03_交付区     # 额外跳过目录（可重复）
    python scan_archived_deps.py --selftest          # 离线自测（临时夹具，不读本机数据）

环境变量:
    HERMES_HOME        记忆库根，默认 ~/.hermes
    ARCH_SCAN_ROOTS    默认扫描根，os.pathsep 分隔；未设则 $HERMES_HOME + 当前目录

退出码: 恒 0（这是检测脚本，结论走 stdout；便于定时任务无条件收集）
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile

try:                       # 跨平台：非 UTF-8 控制台/重定向下不因表情符号崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERMES_HOME = os.path.abspath(os.path.expanduser(
    os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes")))

# 默认扫描的文本扩展名（对齐链路体检的覆盖面：文档/脚本/配置/批处理）
DEFAULT_EXTS = (".md", ".py", ".json", ".jsonl", ".yaml", ".yml", ".txt",
                ".bat", ".ps1", ".sh")
# 默认跳过的目录（版本库/依赖/缓存/构建产物/编辑器元数据）
DEFAULT_SKIP = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
                ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
                ".obsidian", ".idea", ".vscode", "site-packages", ".curator_backups"}
MAX_BYTES = 2_000_000      # 超过 2MB 视为数据文件，不扫（日志/转储）

# 名字边界：前后都不许紧贴文件名字符，防 `old-skill` 命中 `old-skill-extra`
_NAME_FMT = r"(?<![A-Za-z0-9_.\-])(?:%s)(?![A-Za-z0-9_.\-])"

USAGE = """scan_archived_deps — 归档依赖扫描（归档/删除的技能还有谁在引用）

用法:
  python scan_archived_deps.py [选项]

选项:
  --archive-dir DIR   归档区（默认 $HERMES_HOME/skills/.archive）
  --search-root DIR   扫描根，可重复；默认 $ARCH_SCAN_ROOTS 或 $HERMES_HOME + 当前目录
  --ext EXT           文件扩展名，可重复（默认 .md/.py/.json/.jsonl/.yaml/.yml/.txt/.bat/.ps1/.sh）
  --skip-dir NAME     额外跳过的目录名或路径片段，可重复
  --json              只输出 JSON（供 agent/管道消费）
  --limit N           人读报告里每个技能最多列几处引用（默认 8；JSON 不受限）
  --selftest          离线自测（临时夹具；不读也不写本机数据）
  --help              本帮助

输出:
  每个归档技能 → 引用方 `path:line` + 命中行片段；末尾给出"无任何活引用"清单。
  命中行同时出现归档区路径的，归为"归档提及"，单独计数。
退出码: 恒 0（结论走 stdout）
"""


# ── 归档清单 ──────────────────────────────────────────────────────────
def collect_archived(archive_dir):
    """扫归档区，返回 {skill_name: {"aliases": set, "path": abs}} 与错误列表。

    目标名的判定（宁可少收，不可把分类目录当技能）:
      - 目录里有 SKILL.md            → 是技能（目录名 + name: 字段互为别名）
      - 归档区直属的空目录           → 是技能（删剩的空壳，调用方仍可能引用其名字）
      - 其它子目录（分类壳/空 references）→ 不是技能，跳过
    报告以目录名为准（引用方写的通常是名字，不是 SKILL.md 里的 name）。
    """
    skills, errors = {}, []
    archive_dir = os.path.abspath(archive_dir)
    if not os.path.isdir(archive_dir):
        return skills, errors
    for dirpath, dirnames, filenames in os.walk(archive_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_SKIP)
        if os.path.abspath(dirpath) == archive_dir:
            continue
        base = os.path.basename(dirpath)
        skill_md = os.path.join(dirpath, "SKILL.md")
        has_skill_md = os.path.isfile(skill_md)
        is_empty_shell = (not dirnames and not filenames
                          and os.path.dirname(os.path.abspath(dirpath)) == archive_dir)
        if not has_skill_md and not is_empty_shell:
            continue
        aliases = {base}
        if has_skill_md:
            try:
                head = io.open(skill_md, encoding="utf-8", errors="ignore").read(4096)
            except OSError as e:
                errors.append("%s: %s" % (skill_md, e))
                head = ""
            m = re.search(r"^name:\s*(\S+)", head, re.M)
            if m:
                aliases.add(m.group(1).strip().strip("'\""))
        # 同名技能分布在归档子目录里时，用相对路径区分，避免合并丢引用
        key = base
        if key in skills:
            key = os.path.relpath(dirpath, archive_dir).replace(os.sep, "/")
        skills[key] = {"aliases": aliases, "path": dirpath}
    return skills, errors


def _build_matcher(skills):
    """把全部别名合成一条正则（长的优先，防短名截胡），返回 (regex, alias→skill)。"""
    alias_map = {}
    for name, meta in skills.items():
        for a in meta["aliases"]:
            alias_map[a] = name
    if not alias_map:
        return None, alias_map
    ordered = sorted(alias_map, key=lambda s: (-len(s), s))
    return re.compile(_NAME_FMT % "|".join(re.escape(a) for a in ordered)), alias_map


def _is_archive_mention(line, archive_dir):
    """该行是否在"说明归档本身"（路径片段命中归档目录名/绝对路径）。"""
    norm = line.replace("\\", "/")
    arch = archive_dir.replace("\\", "/").rstrip("/")
    if arch and arch in norm:
        return True
    name = os.path.basename(arch)
    return bool(name) and ("/" + name + "/") in norm


# ── 扫描 ──────────────────────────────────────────────────────────────
def scan(archive_dir, search_roots, exts=DEFAULT_EXTS, skip_dirs=(), max_bytes=MAX_BYTES):
    """核心扫描。返回报告 dict（结构即 --json 输出）。

    报告里每个引用方带 root（命中的扫描根）+ path（相对该根的路径）+ line + text，
    这样换机器/换根目录后仍可读，也不会泄露绝对路径。
    """
    archive_dir = os.path.abspath(os.path.expanduser(archive_dir))
    roots = [os.path.abspath(os.path.expanduser(r)) for r in search_roots]
    skills, errors = collect_archived(archive_dir)
    if skills:
        pattern, alias_map = _build_matcher(skills)
    else:
        pattern, alias_map = None, {}
    exts = tuple(e if e.startswith(".") else "." + e for e in exts)
    skip = set(DEFAULT_SKIP) | set(skip_dirs)
    arch_real = os.path.realpath(archive_dir)

    deps = {name: [] for name in skills}
    mentions = 0
    files_scanned = 0
    seen_roots = set()

    for root in roots:
        if not os.path.isdir(root) or root in seen_roots:
            continue
        seen_roots.add(root)
        for dirpath, dirnames, filenames in os.walk(root):
            # 归档区自身不算"活引用方"（否则自己引自己一片绿）
            if os.path.realpath(dirpath).startswith(arch_real):
                dirnames[:] = []
                continue
            dirnames[:] = sorted(d for d in dirnames if d not in skip)
            for fn in sorted(filenames):
                if not fn.lower().endswith(exts):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(fp) > max_bytes:
                        continue
                except OSError:
                    continue
                try:
                    fh = io.open(fp, encoding="utf-8", errors="replace")
                except OSError as e:
                    errors.append("%s: %s" % (fp, e))
                    continue
                files_scanned += 1
                rel = os.path.relpath(fp, root).replace(os.sep, "/")
                try:
                    for lineno, line in enumerate(fh, 1):
                        if pattern is None:
                            break
                        hits = {alias_map[m.group(0)] for m in pattern.finditer(line)}
                        if not hits:
                            continue
                        if _is_archive_mention(line, archive_dir):
                            mentions += len(hits)
                            continue
                        text = line.strip()
                        if len(text) > 160:
                            text = text[:157] + "..."
                        for name in sorted(hits):
                            deps[name].append({"root": root, "path": rel,
                                               "line": lineno, "text": text})
                finally:
                    fh.close()

    unref = sorted(n for n, refs in deps.items() if not refs)
    report = {
        "archive_dir": archive_dir,
        "search_roots": sorted(seen_roots),
        "archived_skills": sorted(skills),
        "files_scanned": files_scanned,
        "archive_mentions": mentions,
        "total_refs": sum(len(v) for v in deps.values()),
        "dependencies": {n: deps[n] for n in sorted(deps) if deps[n]},
        "unreferenced": unref,
        "errors": errors,
    }
    return report


# ── 人读摘要 ──────────────────────────────────────────────────────────
def print_report(report, limit=8):
    print("归档区: %s" % report["archive_dir"])
    print("扫描根: %s" % (", ".join(report["search_roots"]) or "(无可用目录)"))
    print("扫描文件: %d 个" % report["files_scanned"])
    print("归档技能: %d 个 —— %s" % (len(report["archived_skills"]),
                                     ", ".join(report["archived_skills"]) or "(空)"))
    if report["archive_mentions"]:
        print("归档提及（指向归档区本身，不计为活依赖）: %d 处" % report["archive_mentions"])
    print("\n=== 活引用命中 %d 处（涉及 %d 个归档技能）==="
          % (report["total_refs"], len(report["dependencies"])))
    if not report["dependencies"]:
        print("  （无——所有归档技能都没有活引用）")
    for name in sorted(report["dependencies"]):
        refs = report["dependencies"][name]
        print("\n■ %s  （%d 处）" % (name, len(refs)))
        for r in refs[:limit]:
            print("   %s:%d" % (r["path"], r["line"]))
            print("      %s" % r["text"])
        if len(refs) > limit:
            print("   ... 其余 %d 处（用 --json 取全量）" % (len(refs) - limit))
    unref = report["unreferenced"]
    print("\n=== 无任何活引用（可安全留在归档）%d 个 ===" % len(unref))
    print("  " + (", ".join(unref) if unref else "(无)"))
    if report["errors"]:
        print("\n=== 读取失败 %d 处 ===" % len(report["errors"]))
        for e in report["errors"][:5]:
            print("  " + e)


def parse_args(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--archive-dir", default=None)
    ap.add_argument("--search-root", action="append", default=[])
    ap.add_argument("--ext", action="append", default=[])
    ap.add_argument("--skip-dir", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--help", "-h", action="store_true")
    return ap.parse_args(argv)


def default_roots():
    env = os.environ.get("ARCH_SCAN_ROOTS")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    return [HERMES_HOME, os.getcwd()]


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.help:
        print(USAGE)
        return 0
    if args.selftest:
        return selftest()
    try:
        archive_dir = args.archive_dir or os.path.join(HERMES_HOME, "skills", ".archive")
        archive_dir = os.path.abspath(os.path.expanduser(archive_dir))
        if not os.path.isdir(archive_dir):
            print("归档区不存在: %s" % archive_dir)
            print("用 --archive-dir 指定，或设置 HERMES_HOME（见 --help）。")
            return 0
        report = scan(archive_dir,
                      args.search_root or default_roots(),
                      args.ext or DEFAULT_EXTS,
                      args.skip_dir)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_report(report, args.limit)
    except Exception as e:                      # 检测脚本不因异常改变退出码语义
        print("扫描失败: %s: %s" % (type(e).__name__, e))
    return 0


# ── 自测（临时夹具，零外网、零本机依赖）──────────────────────────────
def selftest():
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        print("  %s %s" % ("✅" if cond else "❌", name))

    sandbox = tempfile.mkdtemp(prefix="pp_arch_deps_")
    try:
        home = os.path.join(sandbox, "hermes")
        arch = os.path.join(home, "skills", ".archive")

        def put(rel, text):
            p = os.path.join(home, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with io.open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return p

        # 夹具：两个归档技能（一个带 SKILL.md 别名，一个是删剩的空壳）+ 一个真无人引用的
        put("skills/.archive/old-skill/SKILL.md",
            "---\nname: old-alias\ndescription: 已归档\n---\n正文\n")
        os.makedirs(os.path.join(arch, "gone-skill"), exist_ok=True)
        put("skills/.archive/quiet-skill/SKILL.md", "---\nname: quiet-skill\n---\n")
        put("skills/.archive/category/gone-skill2/SKILL.md", "---\nname: gone-skill2\n---\n")

        put("docs/a.md",
            "见 old-skill 技能\n"                 # 1) 命中
            "old-skill-extra 不算引用\n"          # 2) 词边界：不该命中
            "已移入 skills/.archive/old-skill\n"  # 3) 归档提及：不算活依赖
            "old-alias 别名也算\n")               # 4) 命中（SKILL.md 的 name 别名）
        put("scripts/b.py", "skill_view(name=\"gone-skill\")\n")
        put("scripts/c.sh", "echo old-skill\n")
        put("node_modules/pkg/x.md", "old-skill\n")     # 默认跳过目录
        put("docs/ignored.log", "old-skill\n")          # 不在默认扩展名里，不扫

        report = scan(arch, [home])
        deps = report["dependencies"]
        old = deps.get("old-skill", [])
        check("收齐 4 个归档技能（含分类子目录）", len(report["archived_skills"]) == 4)
        check("old-skill 命中 docs/a.md 第 1/4 行 + scripts/c.sh",
              [r["path"] for r in old] == ["docs/a.md", "docs/a.md", "scripts/c.sh"]
              and [r["line"] for r in old] == [1, 4, 1])
        check("词边界：old-skill-extra 不计入", len(old) == 3)
        check("归档提及单独计数、不进引用方", report["archive_mentions"] == 1)
        check("SKILL.md 的 name 别名也参与匹配",
              any("old-alias" in r["text"] for r in old))
        check("空壳归档目录也被扫到（gone-skill）",
              deps.get("gone-skill", [{}])[0].get("path") == "scripts/b.py")
        check("空壳归档命中的行号正确", deps.get("gone-skill", [{}])[0].get("line") == 1)
        check(".sh 默认在扫描范围内", deps.get("old-skill") and any(
            r["path"] == "scripts/c.sh" for r in old))
        check("默认跳过 node_modules", all("node_modules" not in r["path"]
                                       for refs in deps.values() for r in refs))
        check("无引用清单只含 quiet-skill / gone-skill2",
              report["unreferenced"] == ["gone-skill2", "quiet-skill"])
        check("归档区自身不被当成引用方",
              all(not r["path"].startswith("skills/.archive") for refs in deps.values() for r in refs))

        # --ext 过滤：只扫 .md 时，gone-skill 的 .py 引用消失
        md_only = scan(arch, [home], exts=(".md",))
        check("--ext 过滤生效（.md 之外不扫）",
              "gone-skill" not in md_only["dependencies"])
        check("未列出的扩展名默认不扫（docs/ignored.log）",
              all(r["path"] != "docs/ignored.log"
                  for refs in deps.values() for r in refs))

        # --skip-dir：额外跳过 docs，docs 里的引用消失、其它目录保留
        skipped = scan(arch, [home], skip_dirs=("docs",))
        check("--skip-dir 生效（docs 内引用被排除，c.sh 保留）",
              all(not r["path"].startswith("docs/")
                  for refs in skipped["dependencies"].values() for r in refs)
              and [r["path"] for r in skipped["dependencies"].get("old-skill", [])]
              == ["scripts/c.sh"])

        # 归档区不存在：不抛异常、给出空结论
        empty = scan(os.path.join(sandbox, "nope"), [home])
        check("归档区缺失时返回空清单", empty["archived_skills"] == [] and empty["dependencies"] == {})

        # 端到端：子进程跑 --json，验证 CLI 契约（退出码 0 + 合法 JSON + 无个人路径）
        import subprocess
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__),
             "--archive-dir", arch, "--search-root", home, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        check("CLI --json 退出码为 0", proc.returncode == 0)
        try:
            payload = json.loads(proc.stdout)
        except Exception:
            payload = None
        check("CLI --json 输出可解析", isinstance(payload, dict))
        check("CLI JSON 结构与 scan() 一致",
              isinstance(payload, dict)
              and set(payload) == set(report)
              and payload["archived_skills"] == report["archived_skills"])
        check("引用方路径为相对路径（不泄露绝对路径）",
              all(not os.path.isabs(r["path"])
                  for refs in (payload or {}).get("dependencies", {}).values() for r in refs))

        # 归档区不存在时 CLI 也必须退出 0（检测脚本语义）
        proc2 = subprocess.run(
            [sys.executable, os.path.abspath(__file__),
             "--archive-dir", os.path.join(sandbox, "nope"), "--search-root", home],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        check("归档区缺失时 CLI 仍退出 0", proc2.returncode == 0)
    finally:
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)

    ok = sum(1 for _, c in checks if c)
    print("\nSELFTEST %s %d/%d" % ("OK" if ok == len(checks) else "FAIL", ok, len(checks)))
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
