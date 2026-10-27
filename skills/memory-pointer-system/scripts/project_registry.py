# -*- coding: utf-8 -*-
"""
project_registry.py — 项目注册引擎（L1 只留一条头指针的收编法）
=====================================================================
多项目长的 agent 生态里，最容易烂的是记忆：每开一个项目就往 L1（内置记忆）里塞
一段项目叙述，几轮下来记忆被项目细节挤爆，真正该常驻的规则反而被冲掉。

本引擎把"项目"收编成两件东西：
  ① 注册表一条：mem_rules.json 的 projects.list（slug/name/path/summary/status/
     created/key/head_added）——机器可读，别的脚本按它找项目
  ② L1 一行头指针：`key = skill://<技能> | 一句话`——记忆里只留召回锚点，
     项目细节归它自己的技能/文档（内容分层，不是把记忆压缩成提纲）

因为头指针会直接进 MEMORY.md，本引擎的默认行为是 **dry-run**：只打印将要做的改动，
--commit 才落盘。落盘顺序固定为 备份 → 写记忆 → 写注册表；任何一步失败就按备份回滚，
不会留下"记忆里有指针、注册表里没条目"的半截状态。

用法:
    python project_registry.py --list
    python project_registry.py add --slug demo --name "Demo 项目" \\
        --path "~/work/demo" --summary "一句话说清这项目干什么"
    python project_registry.py add --slug demo --name "Demo 项目" \\
        --path "~/work/demo" --summary "…" --commit
    python project_registry.py --selftest

add 选项:
    --slug      短标识（小写字母/数字/-/_，唯一）        必填
    --name      项目名                                    必填
    --path      项目根目录（支持 ~）                      必填
    --summary   一句话（同时作为头指针摘要，超长按 summary.max_len 截断）
    --key       头指针键名（默认 slug，"-" 转 "_"；须符合 mem_guard 的键名规则）
    --skill     头指针指向的技能名（默认 slug）——项目细节的归处
    --status    active|archived（默认 active）
    --commit    真正落盘（不加则 dry-run）
    --rules-file / --memory-file   覆盖目标文件（默认取 $HERMES_HOME，方便测试）

环境变量:
    HERMES_HOME     记忆库根，默认 ~/.hermes
    PROJECT_RULES   规则表路径，默认本脚本同目录 mem_rules.json
    PROJECT_MEMORY  记忆文件，默认 $HERMES_HOME/memories/MEMORY.md
    PROJECT_SKILLS  技能库根，默认 $HERMES_HOME/skills（只用于提示指针是否可能断链）

退出码: 0 成功（含 dry-run）；1 拒绝/失败（重复 slug、非法参数、写失败）；2 用法错误
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

RULES_FILE = os.environ.get("PROJECT_RULES") or os.path.join(_BASE, "mem_rules.json")
MEMORY_FILE = os.environ.get("PROJECT_MEMORY") or os.path.join(HERMES_HOME, "memories", "MEMORY.md")
SKILLS_ROOT = os.environ.get("PROJECT_SKILLS") or os.path.join(HERMES_HOME, "skills")

# slug/技能名/键名的合法形状：slug 与技能名允许 "-"，键名按 mem_guard 的指针语法
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
SKILL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*$")     # 与 mem_guard POINTER_RE 的键名一致
DEFAULT_SUMMARY_LEN = 40

USAGE = """project_registry — 项目注册（注册表 + L1 一条头指针，默认 dry-run）

用法:
  python project_registry.py --list
  python project_registry.py add --slug SLUG --name NAME --path DIR [选项]
  python project_registry.py --selftest

选项（add）:
  --slug SLUG        短标识（小写字母/数字/-/_，唯一）
  --name NAME        项目名
  --path DIR         项目根目录（支持 ~）
  --summary TEXT     一句话（同时作为头指针摘要，超长按规则表截断）
  --key KEY          头指针键名（默认 slug，"-" 转 "_"）
  --skill SKILL      头指针指向的技能名（默认 slug）——项目细节的归处
  --status S         active | archived（默认 active）
  --commit           真正落盘（不加则只打印计划）
  --rules-file F     规则表（默认 $PROJECT_RULES 或本脚本同目录 mem_rules.json）
  --memory-file F    记忆文件（默认 $PROJECT_MEMORY 或 $HERMES_HOME/memories/MEMORY.md）

通用:
  --list             列已注册项目 + 头指针是否在位
  --selftest         离线自测（临时夹具；不读也不写本机数据）
  --help             本帮助

落盘顺序: 备份(*.bak.<时间戳>) → 写记忆文件 → 写注册表；任一步失败按备份回滚。
退出码: 0 成功 / 1 拒绝或失败 / 2 用法错误
"""


# ── 路径与文件 ────────────────────────────────────────────────────────
def resolve_path(p):
    """展开 ~/$VAR；相对路径按 HERMES_HOME 解析。空值返回 ''。"""
    if not p:
        return ""
    p = os.path.expandvars(os.path.expanduser(p))
    return p if os.path.isabs(p) else os.path.join(HERMES_HOME, p)


def read_text(path, default=""):
    """读文本（不存在返回 default）。"""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def load_json(path):
    """读 JSON。返回 (data, error)；文件不存在时 data={} 且 error=''（首次登记就是这情形）。"""
    if not os.path.exists(path):
        return {}, ""
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


def write_text_atomic(path, text):
    """先写 .tmp 再 os.replace：写到一半断电不会把原文件截断。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def dump_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def backup_file(path, ts):
    """复制一份 <name>.bak.<ts>；源文件不存在返回 None（首次创建无备份可做）。"""
    if not os.path.exists(path):
        return None
    dst = "%s.bak.%s" % (path, ts)
    with io.open(path, "rb") as src:
        payload = src.read()
    with io.open(dst, "wb") as out:
        out.write(payload)
    return dst


def restore_file(path, bak):
    """回滚：有备份就还原；没备份说明是本次新建的，直接删掉。"""
    if bak and os.path.exists(bak):
        with io.open(bak, "rb") as src:
            payload = src.read()
        with io.open(path, "wb") as out:
            out.write(payload)
        return
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


# ── 头指针 ────────────────────────────────────────────────────────────
def fit_summary(summary, max_len):
    """摘要按规则表上限截断（超长指针会被 mem_guard 判 🔴，不如落盘前就切好）。"""
    text = " ".join((summary or "").split())        # 折叠换行/多空格，指针必须单行
    if max_len and len(text) > max_len:
        return text[:max_len - 1] + "…"
    return text


def build_pointer(key, skill, summary):
    """头指针行：与 MEMORY.md 现有指针行格式完全一致（key = scheme://target | 摘要）。"""
    return "%s = skill://%s | %s" % (key, skill, summary)


def append_pointer(memory_text, pointer):
    """在记忆文件末尾追加 § 分隔 + 一行头指针。

    追加在末尾而不是插到文件头：文件头往往是全局规则，项目指针属于"追加事实"，
    而且末尾插入不会动到已有行号（排查历史报告时对得上）。
    """
    body = memory_text or ""
    if body and not body.endswith("\n"):
        body += "\n"
    body += "§\n" if body.strip() else "§\n"
    return body + pointer + "\n"


def pointer_present(memory_text, key):
    """记忆文件里是否已有该键名的指针行。"""
    return bool(re.search(r"(?m)^\s*%s\s*=" % re.escape(key), memory_text or ""))


# ── 注册 ──────────────────────────────────────────────────────────────
def entry_preview(slug, name, path, summary, key, status="active", head_added=False):
    return {
        "slug": slug,
        "name": name,
        "path": path,
        "summary": summary,
        "status": status or "active",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "key": key,
        "head_added": bool(head_added),
    }


def do_add(rules_file, memory_file, slug, name, path, summary="", key=None, skill=None,
           status="active", commit=False, out=None):
    """add 子命令的实现（显式传路径，方便 selftest 指向临时夹具）。返回退出码。"""
    out = out or sys.stdout
    say = lambda s="": print(s, file=out)

    # ① 参数校验：全部在碰文件之前做完
    slug = (slug or "").strip()
    name = (name or "").strip()
    if not SLUG_RE.match(slug):
        say("❌ slug 非法: %r（要求小写字母/数字/-/_，1-40 字符，首字符须字母或数字）" % slug)
        return 1
    if not name:
        say("❌ 缺少 --name")
        return 1
    path = resolve_path(path)
    if not path:
        say("❌ 缺少 --path")
        return 1
    key = (key or slug.replace("-", "_")).strip()
    if not KEY_RE.match(key):
        say("❌ 头指针键名非法: %r（必须是 ^[a-z_][a-z0-9_]*$，与 mem_guard 的指针语法一致）" % key)
        return 1
    skill = (skill or slug).strip()
    if not SKILL_RE.match(skill):
        say("❌ 技能名非法: %r（不能含空格/斜杠，指针里 scheme://target 不允许空格）" % skill)
        return 1

    rules, err = load_json(rules_file)
    if err:
        say("❌ " + err)
        return 1
    max_len = DEFAULT_SUMMARY_LEN
    summ_cfg = rules.get("summary")
    if isinstance(summ_cfg, dict) and isinstance(summ_cfg.get("max_len"), int):
        max_len = summ_cfg["max_len"]
    summary = fit_summary(summary or name, max_len)

    projects = rules.setdefault("projects", {}).setdefault("list", [])
    if not isinstance(projects, list):
        say("❌ 规则表 projects.list 不是数组，先修规则表")
        return 1
    for p in projects:
        if isinstance(p, dict) and p.get("slug") == slug:
            say("❌ 已注册过 slug=%s（幂等：不重复登记）。查现有条目用 --list，"
                "要改名请先手工从 projects.list 移除。" % slug)
            return 1

    entry = entry_preview(slug, name, path, summary, key, status, head_added=bool(commit))
    pointer = build_pointer(key, skill, summary)
    mem_text = read_text(memory_file, "")
    mem_exists = os.path.exists(memory_file)
    already = pointer_present(mem_text, key)

    # ② dry-run：把将要做的事摆清楚（含备份名），不碰盘
    say("%s项目注册（%s）" % ("[dry-run] " if not commit else "", slug))
    say("  规则表  : %s%s" % (rules_file, "" if os.path.exists(rules_file) else "（不存在，commit 时新建）"))
    say("  记忆文件: %s%s" % (memory_file, "" if mem_exists else "（不存在，commit 时新建）"))
    say("  项目路径: %s%s" % (path, "" if os.path.isdir(path) else "（⚠ 目录当前不存在，先登记后建也行）"))
    if not os.path.isdir(os.path.join(SKILLS_ROOT, skill)):
        say("  ⚠ 技能 %s 未在 %s 找到——头指针可能断链（项目细节建好后 mem_guard 会复核）"
            % (skill, SKILLS_ROOT))
    say("  注册表条目:")
    for line in dump_json(entry).strip().splitlines():
        say("    " + line)
    say("  头指针行（§ 分隔，追加在记忆文件末尾）:")
    say("    §")
    say("    " + pointer)
    if already:
        say("  ⚠ 记忆里已有键名 %s 的指针行——提交时不会重复插入，只补注册表条目" % key)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    say("  将执行:")
    say("    1) 备份 %s → %s.bak.%s" % (os.path.basename(rules_file) or rules_file,
                                       os.path.basename(rules_file) or rules_file, ts))
    say("    2) 备份 %s → %s.bak.%s" % (os.path.basename(memory_file) or memory_file,
                                       os.path.basename(memory_file) or memory_file, ts))
    say("    3) 追加 mem_rules.json projects.list 条目（head_added=true）")
    say("    4) 在 MEMORY.md 末尾插入 § + 头指针")

    if not commit:
        say("\n未落盘（dry-run）。确认无误后加 --commit。")
        return 0

    # ③ 落盘：备份 → 写记忆 → 写注册表；失败按备份回滚
    backups = {}
    try:
        backups[rules_file] = backup_file(rules_file, ts)
        backups[memory_file] = backup_file(memory_file, ts)
        for p, b in backups.items():
            say("✅ 已备份: %s" % (b if b else "%s（无原文件，跳过）" % os.path.basename(p)))

        new_mem = mem_text if already else append_pointer(mem_text, pointer)
        if new_mem != mem_text:
            write_text_atomic(memory_file, new_mem)
            say("✅ 已插入头指针: %s" % pointer)
        else:
            say("⏭️  头指针已存在（键名 %s），未重复插入" % key)

        entry["head_added"] = True
        projects.append(entry)
        write_text_atomic(rules_file, dump_json(rules))
        say("✅ 已登记项目: %s（projects.list 共 %d 条）" % (slug, len(projects)))

        # 写完立刻读回校验：JSON 可解析 + 指针在位（写失败要当场发现，别等下次体检）
        back, back_err = load_json(rules_file)
        ok_json = not back_err and any(
            isinstance(p, dict) and p.get("slug") == slug and p.get("head_added")
            for p in back.get("projects", {}).get("list", []))
        ok_mem = pointer_present(read_text(memory_file, ""), key)
        if not (ok_json and ok_mem):
            raise RuntimeError("落盘后校验失败（注册表=%s 指针=%s）" % (ok_json, ok_mem))
    except Exception as e:
        for p, b in backups.items():
            restore_file(p, b)
        say("❌ 落盘失败，已按备份回滚: %s: %s" % (type(e).__name__, e))
        return 1

    say("\n完成。记忆里现在只多了一行头指针；项目细节请放在 skill://%s 或项目目录里。" % skill)
    return 0


# ── 列表 ──────────────────────────────────────────────────────────────
def cmd_list(rules_file=None, memory_file=None, out=None):
    out = out or sys.stdout
    say = lambda s="": print(s, file=out)
    rules_file = resolve_path(rules_file or RULES_FILE)
    memory_file = resolve_path(memory_file or MEMORY_FILE)
    rules, err = load_json(rules_file)
    if err:
        say("❌ " + err)
        return 1
    projects = rules.get("projects", {}).get("list", []) if isinstance(rules.get("projects"), dict) else []
    if not projects:
        say("（未注册任何项目）规则表: %s" % rules_file)
        say("登记: python project_registry.py add --slug X --name Y --path DIR --summary '一句话' --commit")
        return 0
    mem_text = read_text(memory_file, "")
    say("已注册项目 %d 个 —— 规则表: %s" % (len(projects), rules_file))
    say("记忆文件: %s%s" % (memory_file, "" if os.path.exists(memory_file) else "（不存在）"))
    say("")
    say("%-14s %-22s %-9s %s" % ("slug", "name", "status", "头指针"))
    say("-" * 72)
    missing = 0
    for p in projects:
        if not isinstance(p, dict):
            continue
        key = p.get("key") or p.get("slug") or ""
        here = pointer_present(mem_text, key) if key else False
        if not here:
            missing += 1
        say("%-14s %-22s %-9s %s" % (p.get("slug", "?"), (p.get("name") or "?")[:20],
                                     p.get("status", "?"), "✅ " + key if here else "❌ 缺 %s" % key))
        say("    %s%s" % (p.get("path", ""), "" if os.path.isdir(p.get("path", "")) else "（路径不存在）"))
        if p.get("summary"):
            say("    %s" % p["summary"])
    say("")
    if missing:
        say("⚠ %d 个项目在记忆里没有头指针（L1 召回不到它们）：用 add 补，或手工加一行。" % missing)
    else:
        say("✅ 全部项目的头指针都在位")
    return 0


# ── 自测（临时夹具，零外网、零本机依赖）──────────────────────────────
def selftest():
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        print("  %s %s" % ("✅" if cond else "❌", name))

    sandbox = tempfile.mkdtemp(prefix="pp_projreg_")
    try:
        def put(path, text):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return path

        rules_path = os.path.join(sandbox, "mem_rules.json")
        mem_path = os.path.join(sandbox, "memories", "MEMORY.md")
        rules0 = {
            "version": "1.0",
            "summary": {"max_len": 12},
            "schemes": {"skill": {"verify": "skill_dir"}},
            "projects": {"list": []},
        }
        put(rules_path, json.dumps(rules0, ensure_ascii=False, indent=2) + "\n")
        mem0 = "§\nold = skill://old-skill | 老指针\n"
        put(mem_path, mem0)
        rules_before = io.open(rules_path, encoding="utf-8").read()
        mem_before = io.open(mem_path, encoding="utf-8").read()

        # ① dry-run 不写盘
        buf = io.StringIO()
        rc = do_add(rules_path, mem_path, "demo", "演示项目", os.path.join(sandbox, "proj"),
                    summary="演示 dry-run", commit=False, out=buf)
        dry = buf.getvalue()
        check("dry-run 退出码 0", rc == 0)
        check("dry-run 不写规则表", io.open(rules_path, encoding="utf-8").read() == rules_before)
        check("dry-run 不写记忆文件", io.open(mem_path, encoding="utf-8").read() == mem_before)
        check("dry-run 不产生备份",
              not any(".bak." in f for f in os.listdir(sandbox)))
        check("dry-run 打印计划与新指针行", "demo = skill://demo | 演示 dry-run" in dry
              and "--commit" in dry)

        # ② commit 落盘
        buf = io.StringIO()
        rc = do_add(rules_path, mem_path, "demo", "演示项目", os.path.join(sandbox, "proj"),
                    summary="演示 commit", commit=True, out=buf)
        out2 = buf.getvalue()
        rules_after = io.open(rules_path, encoding="utf-8").read()
        mem_after = io.open(mem_path, encoding="utf-8").read()
        check("commit 退出码 0", rc == 0)
        baks = sorted(f for f in os.listdir(sandbox) if f.startswith("mem_rules.json.bak."))
        baks_mem = sorted(f for f in os.listdir(os.path.dirname(mem_path))
                          if f.startswith("MEMORY.md.bak."))
        check("规则表备份已生成（*.bak.<时间戳>）", len(baks) == 1)
        check("记忆文件备份已生成", len(baks_mem) == 1)
        check("备份内容与原文一致",
              io.open(os.path.join(sandbox, baks[0]), encoding="utf-8").read() == rules_before
              and io.open(os.path.join(os.path.dirname(mem_path), baks_mem[0]),
                          encoding="utf-8").read() == mem_before)

        parsed = json.loads(rules_after)
        lst = parsed.get("projects", {}).get("list", [])
        check("追加后 JSON 仍可解析", isinstance(parsed, dict))
        check("projects.list 追加 1 条", len(lst) == 1 and lst[0].get("slug") == "demo")
        check("条目字段齐全",
              set(lst[0]) == {"slug", "name", "path", "summary", "status",
                              "created", "key", "head_added"} and lst[0]["status"] == "active")
        check("head_added 已置 true", lst[0]["head_added"] is True)
        check("注册表其它段原样保留",
              parsed.get("schemes") == rules0["schemes"]
              and parsed.get("summary") == rules0["summary"])
        check("路径存为绝对路径", os.path.isabs(lst[0]["path"]))

        lines = [ln for ln in mem_after.splitlines() if ln.strip()]
        check("头指针格式与现有指针行一致",
              re.match(r"^demo = skill://demo \| 演示 commit$", lines[-1]) is not None)
        check("头指针位于文件末尾且前一行是 §", lines[-2] == "§")
        check("原有记忆内容未被改动", mem_after.startswith(mem0))
        check("commit 输出报告备份与指针", "已备份" in out2 and "已插入头指针" in out2)

        # ③ 幂等：重复 slug 拒绝且不写盘
        rules_before2 = io.open(rules_path, encoding="utf-8").read()
        mem_before2 = io.open(mem_path, encoding="utf-8").read()
        buf = io.StringIO()
        rc = do_add(rules_path, mem_path, "demo", "演示项目", os.path.join(sandbox, "proj"),
                    commit=True, out=buf)
        check("重复 slug 被拒绝（退出码 1）", rc == 1 and "已注册" in buf.getvalue())
        check("重复 slug 不写盘、不产生新备份",
              io.open(rules_path, encoding="utf-8").read() == rules_before2
              and io.open(mem_path, encoding="utf-8").read() == mem_before2
              and len([f for f in os.listdir(sandbox) if ".bak." in f]) == 1)

        # ④ 参数校验
        buf = io.StringIO()
        rc = do_add(rules_path, mem_path, "Bad Slug", "X", sandbox, commit=True, out=buf)
        check("非法 slug 被拒绝", rc == 1 and "slug 非法" in buf.getvalue())
        buf = io.StringIO()
        rc = do_add(rules_path, mem_path, "ok2", "X", sandbox, key="1bad key", commit=True, out=buf)
        check("非法头指针键名被拒绝", rc == 1 and "键名非法" in buf.getvalue())

        # ⑤ 摘要按 summary.max_len 截断（夹具上限 12）
        buf = io.StringIO()
        rc = do_add(rules_path, mem_path, "long-one", "长摘要项目", sandbox,
                    summary="这是一个特别长的项目摘要必须被截断处理", commit=True, out=buf)
        lst = json.loads(io.open(rules_path, encoding="utf-8").read())["projects"]["list"]
        long_entry = [p for p in lst if p["slug"] == "long-one"][0]
        check("摘要按规则表上限截断", rc == 0 and len(long_entry["summary"]) == 12
              and long_entry["summary"].endswith("…"))
        check("截断后的指针行仍单行合法",
              re.search(r"(?m)^long_one = skill://long-one \| .{11}…$",
                        io.open(mem_path, encoding="utf-8").read()) is not None)
        check("slug 的 - 自动转成键名 _", long_entry["key"] == "long_one")

        # ⑥ 记忆文件不存在时自动创建
        mem2 = os.path.join(sandbox, "fresh", "MEMORY.md")
        buf = io.StringIO()
        rc = do_add(rules_path, mem2, "fresh", "新记忆", sandbox, summary="首次", commit=True, out=buf)
        txt2 = io.open(mem2, encoding="utf-8").read() if os.path.exists(mem2) else ""
        check("记忆文件缺失时创建并写入 § + 指针",
              rc == 0 and txt2.splitlines()[0] == "§"
              and txt2.splitlines()[1] == "fresh = skill://fresh | 首次")

        # ⑦ --list 输出
        buf = io.StringIO()
        rc = cmd_list(rules_path, mem_path, out=buf)
        listing = buf.getvalue()
        check("--list 列出已注册项目", rc == 0 and "demo" in listing and "long-one" in listing)
        check("--list 报告头指针在位情况", "✅ demo" in listing or "✅" in listing)

        # ⑧ 规则表损坏时拒绝写盘
        bad_rules = os.path.join(sandbox, "bad.json")
        put(bad_rules, "{ not json")
        buf = io.StringIO()
        rc = do_add(bad_rules, mem_path, "x", "X", sandbox, commit=True, out=buf)
        check("规则表损坏时拒绝落盘", rc == 1 and "无法解析" in buf.getvalue()
              and io.open(bad_rules, encoding="utf-8").read() == "{ not json")

        # ⑨ CLI 层：dry-run 默认、--list、退出码
        import subprocess
        script = os.path.abspath(__file__)
        cli_mem = os.path.join(sandbox, "cli", "MEMORY.md")
        os.makedirs(os.path.dirname(cli_mem), exist_ok=True)
        cli_rules = os.path.join(sandbox, "cli_rules.json")
        put(cli_rules, json.dumps({"projects": {"list": []}}, indent=2) + "\n")
        proc = subprocess.run([sys.executable, script, "add", "--slug", "cli-demo",
                               "--name", "CLI 演示", "--path", sandbox,
                               "--summary", "命令行登记",
                               "--rules-file", cli_rules, "--memory-file", cli_mem],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        check("CLI add 默认 dry-run（退出码 0、不建记忆文件）",
              proc.returncode == 0 and not os.path.exists(cli_mem)
              and "dry-run" in proc.stdout)
        proc = subprocess.run([sys.executable, script, "add", "--slug", "cli-demo",
                               "--name", "CLI 演示", "--path", sandbox,
                               "--summary", "命令行登记", "--commit",
                               "--rules-file", cli_rules, "--memory-file", cli_mem],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        check("CLI add --commit 落盘", proc.returncode == 0 and os.path.exists(cli_mem)
              and "cli_demo = skill://cli-demo | 命令行登记"
              in io.open(cli_mem, encoding="utf-8").read())
        proc = subprocess.run([sys.executable, script, "--list", "--rules-file", cli_rules,
                               "--memory-file", cli_mem],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        check("CLI --list 可读", proc.returncode == 0 and "cli-demo" in proc.stdout)
        proc = subprocess.run([sys.executable, script, "--selftest-does-not-exist"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        check("未知命令给用法并退出 2", proc.returncode == 2 and "用法" in proc.stdout)
    finally:
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)

    ok = sum(1 for _, c in checks if c)
    print("\nSELFTEST %s %d/%d" % ("OK" if ok == len(checks) else "FAIL", ok, len(checks)))
    return 0 if ok == len(checks) else 1


# ── CLI ───────────────────────────────────────────────────────────────
def parse_add_args(argv):
    ap = argparse.ArgumentParser(prog="project_registry.py add", add_help=False)
    ap.add_argument("--slug", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--path", default="")
    ap.add_argument("--summary", default="")
    ap.add_argument("--key", default=None)
    ap.add_argument("--skill", default=None)
    ap.add_argument("--status", default="active", choices=["active", "archived"])
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--rules-file", default=None)
    ap.add_argument("--memory-file", default=None)
    ap.add_argument("--help", "-h", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if not argv or argv[0] in ("-h", "--help"):
            print(USAGE)
            return 0
        if argv[0] == "--selftest":
            return selftest()
        if argv[0] == "--list":
            ap = argparse.ArgumentParser(add_help=False)
            ap.add_argument("--rules-file", default=None)
            ap.add_argument("--memory-file", default=None)
            a, _unknown = ap.parse_known_args(argv[1:])
            return cmd_list(a.rules_file, a.memory_file)
        if argv[0] == "list":
            return cmd_list(None, None)
        if argv[0] == "add":
            a = parse_add_args(argv[1:])
            if a.help:
                print(USAGE)
                return 0
            return do_add(resolve_path(a.rules_file) or RULES_FILE,
                          resolve_path(a.memory_file) or MEMORY_FILE,
                          a.slug, a.name, a.path, a.summary, a.key, a.skill,
                          a.status, a.commit)
        if any(t.startswith("--slug") for t in argv):      # 容忍省略 add 子命令
            a = parse_add_args(argv)
            return do_add(resolve_path(a.rules_file) or RULES_FILE,
                          resolve_path(a.memory_file) or MEMORY_FILE,
                          a.slug, a.name, a.path, a.summary, a.key, a.skill,
                          a.status, a.commit)
        print("未知命令: %s\n" % argv[0])
        print(USAGE)
        return 2
    except SystemExit as e:                # argparse 的参数错也要落成退出码 2
        return 2 if e.code else 0


if __name__ == "__main__":
    sys.exit(main())
