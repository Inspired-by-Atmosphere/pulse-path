# -*- coding: utf-8 -*-
"""
链路体检 v2（2026-08-10 升级，借鉴 skillcheck 设计：分级输出/退出码/误报抑制）

检查三类知识链连通性：
  1. 文件路径引用（原 scan_links 能力）——增强：跳过代码块围栏与 shell 占位符
  2. 技能名引用：文档中 `X` 技能 / skill_view(name='X') → 验证技能存在
  3. fact# 引用：fact#N / fact #N → 输出清单（cron agent 用 fact_store 验证）

用法:
    python scan_links.py [--ignore TYPE] [dir1 dir2 ...]
    --ignore TYPE: path / skill / fact（跳过对应检查，可重复）

输出与退出码:
    0 = 干净（无 error）
    1 = 有悬空/失效引用（error 级）
    warnings 不阻塞（描述性路径等低置信命中）

路径引用语境判断（防误报）:
    - 跳过 ``` 代码块围栏内的引用
    - 跳过含 $( / ${ / <...> / * 等占位符的路径
    - 跳过 file:// / http(s):// 协议路径（非本地盘符）
"""
import io
import os
import re
import sys

# ── 配置（全部可用环境变量覆盖，默认值跨平台、不含个人路径）──
#   HERMES_HOME    记忆库根目录，默认 ~/.hermes
#   SKILLS_ROOT    技能库根（skill:// 引用校验基准），默认 $HERMES_HOME/skills
#   SCAN_DIRS      默认扫描目录，os.pathsep 分隔（命令行给了目录则忽略），
#                  默认 "$HERMES_HOME/skills" + 当前目录
HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes"))
SKILLS_ROOT = os.path.expanduser(os.environ.get("SKILLS_ROOT")
                                 or os.path.join(HERMES_HOME, "skills"))
_env_dirs = os.environ.get("SCAN_DIRS")
DEFAULT_DIRS = (_env_dirs.split(os.pathsep) if _env_dirs
                else [SKILLS_ROOT, os.getcwd()])
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".obsidian", ".curator_backups"}

USAGE = """scan_links — 链路体检：知识链完整性的三级检查

检查三类引用是否可解析：
  1. 路径引用（盘符路径）→ 目标是否存在
  2. 技能名引用（`X` 技能 / skill_view(name='X') / 「X」技能）→ 技能是否存在
  3. fact# 引用 → 输出清单（供上层用 fact 库校验）

用法:
  python scan_links.py                     # 扫默认目录（见下）
  python scan_links.py <目录...>           # 自定义目录
  python scan_links.py --ignore=path       # 跳过某类检查（path/skill/fact，可重复）
  python scan_links.py --help

默认扫描目录: $SCAN_DIRS（os.pathsep 分隔）；未设则为 $SKILLS_ROOT + 当前目录
环境变量:
  HERMES_HOME   默认 ~/.hermes
  SKILLS_ROOT   技能库根，默认 $HERMES_HOME/skills
  SCAN_DIRS     默认扫描目录

退出码: 0 = 干净；1 = 存在 error 级失效引用（warning 不阻塞）

防误报: 跳过 ``` 代码块内的引用、含占位符（$( ${ <...> * 等）的路径、file:// 与 http(s)://
"""
TEXT_EXTS = (".md", ".py", ".txt", ".yaml", ".yml")

# 盘符路径：C:\xxx 或 C:/xxx（排除转义上下文；允许空格与中文标点，尾部文本靠截断验证消解）
PATH_RE = re.compile(r"(?<![A-Za-z0-9_\\])([A-Za-z]:[\\/][^`\"'()<>|*?\n\r]+)")

PLACEHOLDER_MARKERS = ("(", "${", "…", "...", "xxx", "your", "path/to", "example")
SKILL_REF_RE = re.compile(r"(?:skill_view\(\s*name\s*=\s*['\"]([^'\"]+)['\"]|`([^`]+)`\s*技能|「([^」]+)」\s*技能)")
FACT_REF_RE = re.compile(r"fact\s*#\s*(\d+)")


def iter_text_files(root_dir):
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(TEXT_EXTS):
                yield os.path.join(root, f)


def strip_code_blocks(txt):
    """移除 ``` 围栏代码块内容（保留行号对齐的占位）。"""
    lines = txt.split("\n")
    out, in_code = [], False
    for ln in lines:
        if ln.strip().startswith("```"):
            in_code = not in_code
            out.append("")
            continue
        out.append("" if in_code else ln)
    return "\n".join(out)


def build_skill_index():
    """技能清单：name + 分类/name 两种形式（支持 `web/obscura` 式引用）。"""
    names = set()
    for root, dirs, files in os.walk(SKILLS_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f == "SKILL.md":
                p = os.path.join(root, f)
                try:
                    txt = io.open(p, encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                m = re.search(r"^name:\s*(\S+)", txt, re.M)
                if m:
                    names.add(m.group(1).strip())
                names.add(os.path.basename(root))
                # 分类前缀形式：父目录名/技能名
                parent = os.path.basename(os.path.dirname(root))
                if parent and parent not in SKIP_DIRS:
                    names.add(parent + "/" + os.path.basename(root))
    return names


def path_exists_fuzzy(ref):
    """路径存在性（模糊）：完整存在即真；否则逐级截断尾部段验证——
    处理「路径 + 附加文本」（如 `C:\\...\\foo，端口`、`C:\\Program Files` 截断）。
    最小保留 2 级（盘符 + 一个目录），防 C:\\ 通配放行。"""
    if os.path.exists(ref):
        return True
    parts = ref.replace("/", os.sep).split(os.sep)
    for i in range(len(parts) - 1, 1, -1):
        candidate = os.sep.join(parts[:i])
        if os.path.exists(candidate):
            return True
    return False


def scan_paths(root_dir):
    """路径悬空检查（跳过代码块与占位符）。返回 [(file, ref), ...]"""
    dangling = []
    for path in iter_text_files(root_dir):
        try:
            txt = io.open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        txt = strip_code_blocks(txt)
        for m in PATH_RE.finditer(txt):
            ref = m.group(1).replace("\\", os.sep).rstrip(".,;:，。；：、")
            if ref.endswith(("\\", "/")):
                continue
            if any(mk in ref for mk in PLACEHOLDER_MARKERS):
                continue
            if not path_exists_fuzzy(ref):
                dangling.append((path, m.group(1)))
    return dangling


def scan_skill_refs(root_dir, skill_names):
    """技能名引用检查。返回失效引用 [(file, skill_name), ...]"""
    bad = []
    for path in iter_text_files(root_dir):
        try:
            txt = io.open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        txt = strip_code_blocks(txt)
        for m in SKILL_REF_RE.finditer(txt):
            name = next((g for g in m.groups() if g), None)
            if not name:
                continue
            # 模板占位符（<技能名> 等）与示例单字母（「X」技能）跳过
            if "<" in name or ">" in name or (len(name) == 1 and name.isupper()):
                continue
            if name not in skill_names:
                bad.append((path, name))
    return bad


def scan_fact_refs(root_dir):
    """fact# 引用收集。返回 [(file, fact_id), ...]（供 cron agent 用 fact_store 验证）"""
    refs = []
    for path in iter_text_files(root_dir):
        try:
            txt = io.open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for m in FACT_REF_RE.finditer(txt):
            refs.append((path, m.group(1)))
    return refs


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(USAGE)
        sys.exit(0)
    ignores = {a.split("=")[-1] for a in sys.argv[1:] if a.startswith("--ignore")}
    dirs = args or DEFAULT_DIRS

    skill_names = build_skill_index() if "skill" not in ignores else set()
    print(f"技能库索引: {len(skill_names)} 个技能名")

    total_bad = 0
    for d in dirs:
        if not os.path.isdir(d):
            print(f"跳过（不存在）: {d}")
            continue
        if "path" not in ignores:
            bad = scan_paths(d)
            if bad:
                total_bad += len(bad)
                print(f"=== [{os.path.basename(d)}] 路径悬空引用: {len(bad)} 处 ===")
                for src, ref in bad[:15]:
                    print(f"  {os.path.basename(src)}  →  {ref}")
                if len(bad) > 15:
                    print(f"  ... 其余 {len(bad) - 15} 条")
        if "skill" not in ignores:
            bad = scan_skill_refs(d, skill_names)
            if bad:
                total_bad += len(bad)
                print(f"=== [{os.path.basename(d)}] 技能名引用失效: {len(bad)} 处 ===")
                for src, name in bad[:15]:
                    print(f"  {os.path.basename(src)}  →  技能「{name}」不存在")
                if len(bad) > 15:
                    print(f"  ... 其余 {len(bad) - 15} 条")
        if "fact" not in ignores:
            refs = scan_fact_refs(d)
            if refs:
                ids = sorted({fid for _, fid in refs}, key=int)
                print(f"=== [{os.path.basename(d)}] fact# 引用清单（{len(refs)} 处，涉及 fact {ids}）===")
                print("  （由 cron agent 用 fact_store probe 验证存在性）")

    print(f"\n共 {total_bad} 处 error 级失效引用" if total_bad else "\n✅ 无失效引用")
    sys.exit(1 if total_bad else 0)


if __name__ == "__main__":
    main()
