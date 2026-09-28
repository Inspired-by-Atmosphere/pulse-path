# -*- coding: utf-8 -*-
"""
mem_guard.py v2 — 记忆指针保卫系统（声明式规则表 + 四段管道引擎）
=====================================================================
v2 架构（2026-08-19 用户拍板"规则表+单引擎"，替代 v1 打补丁式）：

    引擎只做 4 件事，业务判断全部查 mem_rules.json：
    ① parse   解析全部指针 → [Pointer(key, scheme, target, summary, line_no)]
    ② verify  按规则表分发到验证器注册表（skill_dir/path_exist/env_key/...）
    ③ act     失败 → 按规则表 autofix 列表尝试自动修复（case_fix/sep_fix/...）
    ④ report  分级输出（🔴人工决策/🟡自动修复/✅正常/?待全量版）+ 静默逻辑

报告分级（用户 8/19 拍板）:
    🔴 人工决策级：决策/逻辑链/需理解省略语义的复杂问题 → QQ 上报（cron exit=1 投递）
    🟡 自动修复级：明显/单一问题（大小写/分隔符/唯一近似）→ 自动修 + 记入报告，不投递
    🟢 正常：全部 ✓ → 静默（cron no_agent 空输出不投递）

扩展方式（不臃肿）:
    加新 scheme    → mem_rules.json 加 3 行（verify/autofix/report）
    加新验证器     → 引擎注册表加 1 个纯函数（校验规则数据化，引擎无业务 if）
    加新问题检测   → damage.detect 加 1 条

用法:
    python mem_guard.py             # 体检 + 自动修复 + 报告（cron 模式）
    python mem_guard.py --check     # 只体检不写回（dry-run）
    python mem_guard.py --verbose   # 全正常也打印报告（人工自查用）
"""
import io
import json
import os
import re
import sys

# ── 路径配置 ──
# 全部可用环境变量覆盖；默认值跨平台、不含任何个人路径。
#   HERMES_HOME      记忆库根目录（默认 ~/.hermes）
#   MEM_GUARD_MEMORY 指针文件（默认 $HERMES_HOME/memories/MEMORY.md）
#   MEM_GUARD_RULES  规则表（默认本脚本同目录 mem_rules.json）
#   MEM_GUARD_SKILLS 技能库根（默认 $HERMES_HOME/skills）
#   MEM_GUARD_SCRIPTS 脚本库根（默认 $HERMES_HOME/scripts）
#   MEM_GUARD_ENV     .env 文件（默认 $HERMES_HOME/.env）
#   MEM_GUARD_CONFIG  config.yaml（默认 $HERMES_HOME/config.yaml）
# 规则表里的相对路径按 HERMES_HOME 解析，${VAR} 会展开环境变量。
# HERMES_HOME 无论传入相对还是绝对路径，都会被规范化为绝对路径——否则规则表里的
# ${HERMES_HOME}/... 展开出相对路径后会被再拼接一次，水位检查就会指向不存在的文件。
HERMES_HOME = os.path.abspath(os.path.expanduser(os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes")))
os.environ["HERMES_HOME"] = HERMES_HOME   # 规范化后写回环境：规则表里的 ${HERMES_HOME} 由 expandvars 展开，读到的是环境原值
_BASE = os.path.dirname(os.path.abspath(__file__))


def _p(env_name, default_rel):
    """环境变量优先；否则按 HERMES_HOME 拼相对路径。"""
    v = os.environ.get(env_name)
    return v if v else os.path.join(HERMES_HOME, default_rel)


def resolve_path(p):
    """展开 ~/$VAR，相对路径按 HERMES_HOME 解析。空值返回 ''。"""
    if not p:
        return ""
    p = os.path.expandvars(os.path.expanduser(p))
    if not os.path.isabs(p):
        p = os.path.join(HERMES_HOME, p)
    return p


MEMORY_FILE = _p("MEM_GUARD_MEMORY", os.path.join("memories", "MEMORY.md"))
RULES_FILE = os.environ.get("MEM_GUARD_RULES") or os.path.join(_BASE, "mem_rules.json")
SKILLS_ROOT = _p("MEM_GUARD_SKILLS", "skills")
SCRIPTS_ROOT = _p("MEM_GUARD_SCRIPTS", "scripts")
ENV_FILE = _p("MEM_GUARD_ENV", ".env")
CONFIG_FILE = _p("MEM_GUARD_CONFIG", "config.yaml")

# 指针行：key = scheme://target | 摘要
POINTER_RE = re.compile(r"^([a-z_][a-z0-9_]*) = ([a-z]+)://(\S+?) \| (.+)$")
# 疑似损坏行：含 "key = ... | 摘要" 结构但无合法 scheme://（压缩破坏/手误）
MANGLED_RE = re.compile(r"^([a-z_][a-z0-9_]*) = (.+?) \| (.+)$")

DEFAULT_RULES = {
    "schemes": {
        "skill": {"verify": "skill_dir", "autofix": ["case_fix", "unique_approx"], "report": "red_if_unfixable"},
        "doc": {"verify": "path_exist", "autofix": ["sep_fix"], "report": "red_if_unfixable"},
        "script": {"verify": "scripts_dir", "autofix": [], "report": "red"},
        "cfg": {"verify": "env_key", "autofix": [], "report": "red"},
        "env": {"verify": "env_key", "autofix": [], "report": "red"},
        "viking": {"verify": "viking_uri", "autofix": [], "report": "pending"},
        "fact": {"verify": "fact_id", "autofix": [], "report": "pending"},
        "rule": {"verify": "rule_doc", "autofix": [], "report": "pending"},
        "cron": {"verify": "cron_id", "autofix": [], "report": "pending"},
        "mem": {"verify": "mem_key", "autofix": [], "report": "pending"},
    },
    "damage": {"detect": ["pointer_mangled", "dup_key"], "action": "red"},
    "content_scans": {},
    "watermark": {"enabled": True, "threshold": 85, "action": "report_list"},
    "output": {"quiet_when_clean": True},
}


# ══════════════════════════════════════════════════════════════
# ① parse
# ══════════════════════════════════════════════════════════════
def parse_pointers(txt):
    """解析指针行。返回 (pointers, mangled_lines, other_lines)。"""
    pointers = []
    mangled = []   # (line_no, key, content)
    others = []    # 非指针行（旧格式条目等）
    for i, ln in enumerate(txt.split("\n"), 1):
        s = ln.strip()
        if not s or s.startswith("#") or s == "§":
            continue
        m = POINTER_RE.match(s)
        if m:
            pointers.append({
                "line": i, "key": m.group(1), "scheme": m.group(2),
                "target": m.group(3), "summary": m.group(4), "raw": s,
            })
            continue
        mm = MANGLED_RE.match(s)
        if mm:
            mangled.append({"line": i, "key": mm.group(1), "content": mm.group(2), "raw": s})
            continue
        others.append({"line": i, "raw": s})
    return pointers, mangled, others


# ══════════════════════════════════════════════════════════════
# ② verify — 验证器注册表（纯函数，规则表按名调用）
# ══════════════════════════════════════════════════════════════
def v_skill_dir(target):
    """skill://name → skills/**/name/SKILL.md 或 skills/name/"""
    for root, dirs, files in os.walk(SKILLS_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".curator_backups")]
        if os.path.basename(root) == target and "SKILL.md" in files:
            return True, os.path.join(root, "SKILL.md")
    if os.path.isdir(os.path.join(SKILLS_ROOT, target)):
        return True, os.path.join(SKILLS_ROOT, target)
    return False, f"技能 {target} 不存在于 {SKILLS_ROOT}"


def v_path_exist(target):
    """doc://路径 → 存在性（容忍 / 与 \\ 混用）。"""
    p = target.replace("/", os.sep)
    if os.path.exists(p):
        return True, p
    alt = target.replace("\\", "/")
    if os.path.exists(alt):
        return True, alt
    return False, f"路径不存在: {target}"


def v_scripts_dir(target):
    """script://name → scripts/ 下存在。"""
    p = os.path.join(SCRIPTS_ROOT, target)
    if os.path.exists(p):
        return True, p
    # 兼容带子目录形式
    if os.path.exists(os.path.join(SCRIPTS_ROOT, target.replace("/", os.sep))):
        return True, os.path.join(SCRIPTS_ROOT, target.replace("/", os.sep))
    return False, f"脚本不存在: {target}"


def v_env_key(target):
    """cfg://KEY / env://KEY → .env 有对应 KEY（兼容大小写）。"""
    if not os.path.exists(ENV_FILE):
        return False, f".env 不存在"
    with io.open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
        env_txt = f.read()
    if re.search(rf"^{re.escape(target.upper())}\s*=", env_txt, re.M):
        return True, f".env 含 {target.upper()}"
    if re.search(rf"^{re.escape(target)}\s*=", env_txt, re.M):
        return True, f".env 含 {target}"
    # config.yaml 顶层 key
    if os.path.exists(CONFIG_FILE):
        with io.open(CONFIG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            cfg_txt = f.read()
        if re.search(rf"^{re.escape(target)}\s*:", cfg_txt, re.M):
            return True, f"config.yaml 含 {target}"
    return False, f".env/config.yaml 无配置 {target}"


def v_viking_uri(target):
    return None, "viking 验证留全量版"


def v_fact_id(target):
    return None, "fact 验证留全量版"


def v_rule_doc(target):
    return None, "rule 验证留全量版"


def v_cron_id(target):
    return None, "cron 验证留全量版"


def v_mem_key(target):
    return None, "mem 验证留全量版"


VERIFIERS = {
    "skill_dir": v_skill_dir, "path_exist": v_path_exist, "scripts_dir": v_scripts_dir,
    "env_key": v_env_key, "viking_uri": v_viking_uri, "fact_id": v_fact_id,
    "rule_doc": v_rule_doc, "cron_id": v_cron_id, "mem_key": v_mem_key,
}


# ══════════════════════════════════════════════════════════════
# ③ act — 自动修复器注册表（纯函数：入 target 出修复后 target 或 None）
# ══════════════════════════════════════════════════════════════
def f_case_fix(scheme, target):
    """大小写唯一匹配（skill 名等）。"""
    if scheme != "skill":
        return None, None
    candidates = []
    for root, dirs, files in os.walk(SKILLS_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".curator_backups")]
        base = os.path.basename(root)
        if base.lower() == target.lower() and base != target and "SKILL.md" in files:
            candidates.append(base)
    if len(candidates) == 1:
        return candidates[0], f"技能名大小写修正: {target} → {candidates[0]}"
    return None, None


def f_unique_approx(scheme, target):
    """唯一近似匹配：去掉连字符/下划线后唯一命中的技能。"""
    if scheme != "skill":
        return None, None
    norm = re.sub(r"[-_]", "", target).lower()
    candidates = []
    for root, dirs, files in os.walk(SKILLS_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".curator_backups")]
        base = os.path.basename(root)
        if re.sub(r"[-_]", "", base).lower() == norm and base != target and "SKILL.md" in files:
            candidates.append(base)
    if len(candidates) == 1:
        return candidates[0], f"技能名近似修正: {target} → {candidates[0]}"
    return None, None


def f_sep_fix(scheme, target):
    """路径分隔符归一（doc）。"""
    if scheme != "doc":
        return None, None
    alt = target.replace("/", "\\") if "/" in target else target.replace("\\", "/")
    if alt != target and os.path.exists(alt.replace("/", os.sep)):
        return alt, f"路径分隔符修正: {target} → {alt}"
    return None, None


FIXERS = {"case_fix": f_case_fix, "unique_approx": f_unique_approx, "sep_fix": f_sep_fix}


# ══════════════════════════════════════════════════════════════
# ④ report + 主流程
# ══════════════════════════════════════════════════════════════
def load_rules():
    try:
        with io.open(RULES_FILE, "r", encoding="utf-8") as f:
            rules = json.load(f)
        # 深合并默认值（规则表缺字段时用默认）
        merged = json.loads(json.dumps(DEFAULT_RULES))
        for k, v in rules.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        return merged
    except Exception as e:
        print(f"⚠️ 规则表加载失败（{e}），使用内置默认规则")
        return json.loads(json.dumps(DEFAULT_RULES))


def check_watermark(rules):
    """水位检查：读 watermark.files（默认 MEMORY.md）使用率（按 Unicode 字符数，与 memory 工具口径一致）。
    超阈值文件生成整理清单（按 § 分段取首行摘要）。返回 [(name, msg)]，空=全部正常。
    2026-08-20 扩展：支持多文件监控（MEMORY/USER）+ 报告条目清单（用户拍板阈值 80%）。"""
    if not rules.get("watermark", {}).get("enabled", True):
        return []
    threshold = rules["watermark"].get("threshold", 80)
    files = rules["watermark"].get("files")
    if not files:
        files = [{"name": "MEMORY", "path": MEMORY_FILE, "limit": rules["watermark"].get("limit", 6000)}]
    out = []
    for f in files:
        try:
            with io.open(resolve_path(f.get("path")) or MEMORY_FILE, "r", encoding="utf-8") as fh:
                txt = fh.read()
            chars = len(txt)
            limit = f.get("limit", rules["watermark"].get("limit", 6000))
            pct = int(chars * 100 / limit)
            if pct >= threshold:
                items = [seg.strip() for seg in txt.split("\u00a7") if seg.strip()]
                heads = []
                for seg in items:
                    h = seg.split("\n", 1)[0].strip()
                    heads.append(h[:22] + "\u2026" if len(h) > 22 else h)
                preview = "\n".join(f"  · {h}" for h in heads[:15])
                more = f"\n  …共 {len(heads)} 条" if len(heads) > 15 else ""
                msg = (f"\u26a0\ufe0f {f['name']} 记忆水位 {pct}%（{chars}/{limit}），"
                       f"\u2265{threshold}% 需整理，当前条目清单：\n{preview}{more}")
                out.append((f["name"], msg))
        except Exception as e:
            out.append((f["name"], f"\u26a0\ufe0f {f['name']} 读取失败（{e}）"))
    return out


def run(dry=False, verbose=False):
    rules = load_rules()
    if not os.path.exists(MEMORY_FILE):
        print(f"⚠️ 指针文件不存在: {MEMORY_FILE}\n"
              f"   请设置 MEM_GUARD_MEMORY 或 HERMES_HOME 指向你的记忆库（见 --help）")
        return 0
    txt = io.open(MEMORY_FILE, "r", encoding="utf-8", errors="ignore").read()
    pointers, mangled, others = parse_pointers(txt)
    lines = txt.split("\n")

    red = []       # 人工决策级
    yellow = []    # 自动修复级
    ok_count = 0
    pending_count = 0

    # ── 指针验证 ──
    for p in pointers:
        key, scheme, target, summary, line = p["key"], p["scheme"], p["target"], p["summary"], p["line"]
        srule = rules["schemes"].get(scheme)
        if not srule:
            red.append((line, key, f"非法 scheme「{scheme}」（合法: {','.join(sorted(rules['schemes']))}）"))
            continue

        vname = srule["verify"]
        if vname not in VERIFIERS:
            red.append((line, key, f"规则表引用未知验证器「{vname}」（引擎无此原语）"))
            continue

        exists, detail = VERIFIERS[vname](target)
        if exists is None:
            pending_count += 1
            continue
        if exists:
            ok_count += 1
            continue

        # 失败 → 尝试自动修复（按规则表 autofix 顺序）
        fixed = False
        for fixname in srule.get("autofix", []):
            fixer = FIXERS.get(fixname)
            if not fixer:
                continue
            new_target, reason = fixer(scheme, target)
            if new_target:
                new_line = f"{key} = {scheme}://{new_target} | {summary}"
                if not dry:
                    lines[p["line"] - 1] = new_line
                yellow.append((line, key, f"🔧 {reason}"))
                fixed = True
                break
        if fixed:
            continue

        # 不可修 → 按 report 策略分级
        report_policy = srule.get("report", "red")
        if report_policy == "pending":
            pending_count += 1
        else:
            red.append((line, key, f"断链: {scheme}://{target}（{detail}）"))

    # ── 疑似损坏行检测 ──
    for m in mangled:
        red.append((m["line"], m["key"], f"疑似损坏指针（缺合法 scheme://）: {m['content'][:40]}"))

    # ── key 冲突检测 ──
    seen = {}
    for p in pointers:
        if p["key"] in seen:
            red.append((p["line"], p["key"], f"key 重复（首次出现于 L{seen[p['key']]}）"))
        else:
            seen[p["key"]] = p["line"]

    # ── 水位 ──
    for _wname, wm_msg in check_watermark(rules):
        red.append((0, "watermark", wm_msg))

    # 写回（仅非 dry 且有任何自动修复）
    if not dry and yellow:
        io.open(MEMORY_FILE, "w", encoding="utf-8", newline="\n").write("\n".join(lines))

    # ── 报告 ──
    now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"🛡 mem_guard v2 体检 {now}"]
    if red:
        parts.append(f"🔴 需人工决策 {len(red)} 条:")
        for line, k, msg in red:
            parts.append(f"  {'L' + str(line) if line else ''} {k}: {msg}")
    if yellow:
        parts.append(f"🟡 自动修复 {len(yellow)} 条:")
        for line, k, msg in yellow:
            parts.append(f"  L{line} {k}: {msg}")
    parts.append(f"✅ 正常 {ok_count} | ? 待全量版 {pending_count} | 指针总数 {len(pointers)}")
    report = "\n".join(parts)

    # 落报告文件（始终记录，可回溯）
    try:
        rf = resolve_path(rules.get("output", {}).get("report_file", ""))
        if rf:
            os.makedirs(os.path.dirname(rf), exist_ok=True)
            with io.open(rf, "a", encoding="utf-8") as f:
                f.write(report + "\n\n")
    except Exception:
        pass

    # 静默逻辑：非 verbose 且无异常 → 空输出
    quiet = rules.get("output", {}).get("quiet_when_clean", True)
    if quiet and not red and not yellow:
        if not verbose:
            print("", end="")
            sys.exit(0)

    print(report)
    # 2026-08-27 修正：恒 exit 0（检测/写入型脚本问题经 stdout 传递，非零退出被 cron 误报为脚本失败）
    sys.exit(0)


USAGE = """mem_guard v2 — 记忆指针保卫系统（声明式规则表 + 四段管道引擎）

用法:
  python mem_guard.py             # 体检 + 自动修复 + 报告
  python mem_guard.py --check     # 只体检不写回（dry-run）
  python mem_guard.py --verbose   # 全正常也打印报告（人工自查用）
  python mem_guard.py --help      # 本帮助

环境变量（默认值跨平台，不含个人路径）:
  HERMES_HOME       记忆库根目录，默认 ~/.hermes
  MEM_GUARD_MEMORY  指针文件，默认 $HERMES_HOME/memories/MEMORY.md
  MEM_GUARD_RULES   规则表，默认本脚本同目录 mem_rules.json
  MEM_GUARD_SKILLS  技能库根，默认 $HERMES_HOME/skills
  MEM_GUARD_SCRIPTS 脚本库根，默认 $HERMES_HOME/scripts
  MEM_GUARD_ENV     .env，默认 $HERMES_HOME/.env
  MEM_GUARD_CONFIG  config.yaml，默认 $HERMES_HOME/config.yaml

当前生效路径:
  MEMORY_FILE   = {memory}
  RULES_FILE    = {rules}
  SKILLS_ROOT   = {skills}

退出码: 恒 0（问题经 stdout 传递；检测型脚本非零退出会被调度器误报为失败）
"""


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(USAGE.format(memory=MEMORY_FILE, rules=RULES_FILE, skills=SKILLS_ROOT))
        sys.exit(0)
    dry = "--check" in args
    verbose = "--verbose" in args
    run(dry=dry, verbose=verbose)


if __name__ == "__main__":
    main()
