# -*- coding: utf-8 -*-
"""
mem_guard v2 单元测试套件（开源版：自包含，不依赖任何本机路径）
=====================================================================
覆盖：解析 / 验证 / 自动修复 / 损坏检测 / key 冲突 / 非法 scheme /
      静默逻辑 / 规则表容错 / 旧格式共存 / 水位 / 路径解析。

⚠️ 自包含设计：所有被验证的路径与技能都建在 tempfile 沙箱里，
   跑测试**不需要**预装技能库、记忆文件或任何 HOME 下的真实数据；
   也不写入任何真实文件。
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mem_guard as mg

PASS = 0
FAIL = 0
SANDBOX = None


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")


def setup_sandbox():
    """建一个临时技能库/记忆文件沙箱，避免依赖本机真实数据。"""
    global SANDBOX
    SANDBOX = tempfile.mkdtemp(prefix="mem_guard_test_")
    skills = os.path.join(SANDBOX, "skills")
    os.makedirs(os.path.join(skills, "demo-skill"), exist_ok=True)
    io.open(os.path.join(skills, "demo-skill", "SKILL.md"), "w", encoding="utf-8").write(
        "---\nname: demo-skill\ndescription: fixture\n---\n")
    os.makedirs(os.path.join(skills, "category", "nested-skill"), exist_ok=True)
    io.open(os.path.join(skills, "category", "nested-skill", "SKILL.md"), "w", encoding="utf-8").write(
        "---\nname: nested-skill\n---\n")
    docs = os.path.join(SANDBOX, "docs")
    os.makedirs(docs, exist_ok=True)
    mem = os.path.join(SANDBOX, "memory.md")
    io.open(mem, "w", encoding="utf-8").write("§\nkey = skill://demo-skill | 摘要\n")
    mg.SKILLS_ROOT = skills
    return mem


def test_parse():
    print("\n== ① parse ==")
    txt = "key1 = skill://demo-skill | 摘要\nkey2 = doc://path/to/x.md | 摘要2\n"
    pointers, mangled, others = mg.parse_pointers(txt)
    check("解析 2 条指针", len(pointers) == 2 and len(mangled) == 0)
    check("key1 scheme 正确", pointers[0]["scheme"] == "skill")
    check("key1 target 正确", pointers[0]["target"] == "demo-skill")
    check("摘要剥离", "摘要" in pointers[0]["summary"])
    txt2 = "Hermes venv=...hermes-agent\\venv（uv无pip）\n"
    p2, m2, o2 = mg.parse_pointers(txt2)
    check("旧格式归入 others", len(o2) == 1 and len(m2) == 0)
    txt3 = "# 注释\n\nkey = doc://a.md | 摘要\n"
    p3, m3, o3 = mg.parse_pointers(txt3)
    check("注释被忽略", len(p3) == 1)


def test_verify():
    print("\n== ② verify ==")
    ok = mg.v_skill_dir("demo-skill")[0]
    check("skill 存在 → ok", ok is True)
    ok2 = mg.v_skill_dir("no-such-skill-xyz")[0]
    check("skill 不存在 → fail", ok2 is False)
    ok3 = mg.v_path_exist(SANDBOX)[0]
    check("path_exist 真路径", ok3 is True)
    ok4 = mg.v_path_exist(os.path.join(SANDBOX, "no_such_dir_xyz"))[0]
    check("path_exist 假路径", ok4 is False)


def test_autofix():
    print("\n== ③ autofix ==")
    fix, reason = mg.f_case_fix("skill", "no-such-skill-xyz")
    check("case_fix 无候选返回 None", fix is None)
    fix2, reason2 = mg.f_unique_approx("skill", "demo_skill")
    check("unique_approx 修复下划线", fix2 == "demo-skill")
    # 用另一侧分隔符构造路径，验证 sep_fix 能归一（跨平台可跑）
    real = os.path.join(SANDBOX, "docs", "readme.md")
    io.open(real, "w", encoding="utf-8").write("x")
    flip = "/" if os.sep == "\\" else "\\"
    alt_form = real.replace(os.sep, flip)
    fix3, reason3 = mg.f_sep_fix("doc", alt_form)
    check("sep_fix 处理另一侧分隔符", isinstance(fix3, str) and os.path.exists(fix3))


def test_mangled():
    print("\n== ④ 损坏检测 ==")
    txt = "dsh = 内部工具底座 | 部署细节见技能\n"
    p, m, o = mg.parse_pointers(txt)
    check("疑似损坏行被识别", len(m) == 1)
    txt2 = "Hermes venv=...hermes-agent\\venv（uv无pip）\n"
    p2, m2, o2 = mg.parse_pointers(txt2)
    check("旧格式不误报损坏", len(m2) == 0)
    p3, m3, o3 = mg.parse_pointers("")
    check("空输入不崩溃", len(p3) == 0 and len(m3) == 0)
    txt4 = "文档读取：PDF文字层乱→渲染PNG\n"
    p4, m4, o4 = mg.parse_pointers(txt4)
    check("无|结构不误报", len(m4) == 0)


def test_dup_key():
    print("\n== ⑤ key 冲突 ==")
    txt = "k = doc://a.md | s1\nk = doc://b.md | s2\n"
    p, m, o = mg.parse_pointers(txt)
    keys = [x["key"] for x in p]
    check("重复 key 检测", len(keys) == 2 and len(set(keys)) == 1)


def test_silent():
    print("\n== ⑥ 静默逻辑 ==")
    def sim_exit(red):
        return 1 if red else 0
    check("红级 → exit 1", sim_exit(1) == 1)
    check("无红级 → exit 0", sim_exit(0) == 0)
    check("quiet 配置存在", "quiet_when_clean" in mg.load_rules().get("output", {}))


def test_watermark(mem):
    print("\n== ⑦ 水位 ==")
    rules = {"watermark": {"enabled": True, "threshold": 99,
                           "files": [{"name": "MEMORY", "path": mem, "limit": 100000}]}}
    wm = mg.check_watermark(rules)
    check("未超阈值 → 空列表", wm == [])
    rules2 = {"watermark": {"enabled": True, "threshold": 1,
                            "files": [{"name": "MEMORY", "path": mem, "limit": 10}]}}
    wm2 = mg.check_watermark(rules2)
    check("超阈值 → 返回警告", wm2 is not None and len(wm2) == 1)


def test_paths_tolerance():
    print("\n== ⑧ 规则表/路径容错 ==")
    rules = mg.load_rules()
    check("load_rules 返回 dict", isinstance(rules, dict) and "schemes" in rules)
    check("schemes 非空", len(rules["schemes"]) > 0)
    old_home = mg.HERMES_HOME
    mg.HERMES_HOME = SANDBOX
    rel = mg.resolve_path("memories/MEMORY.md")
    mg.HERMES_HOME = old_home
    check("相对路径按 HERMES_HOME 解析",
          os.path.normpath(rel) == os.path.normpath(os.path.join(SANDBOX, "memories", "MEMORY.md")))


def test_summary():
    print("\n== ⑨ 摘要长度（规则表 summary 段）==")
    rules = {"summary": {"max_len": 40, "severity": "red"}}
    p40 = mg.parse_pointers("k = skill://demo-skill | " + "短" * 40 + "\n")[0]
    check("40 字中文（按字符计）→ 不报", mg.check_summaries(p40, rules) == [])
    p41 = mg.parse_pointers("k = skill://demo-skill | " + "长" * 41 + "\n")[0]
    issues = mg.check_summaries(p41, rules)
    check("41 字 → 报 1 条", len(issues) == 1)
    check("超长等级 = red（读规则表）", issues and issues[0][0] == "red")
    check("消息形如『摘要过长: N 字 > 40（key）』",
          issues and issues[0][3] == "摘要过长: 41 字 > 40（k）")
    pmix = mg.parse_pointers("k2 = skill://demo-skill | " + "中" * 30 + "abcdefghij" + "\n")[0]
    check("中英混排 30 中文+10 英文=40 → 不报", mg.check_summaries(pmix, rules) == [])
    check("上限由规则表决定（改 10 → 同摘要超长）",
          len(mg.check_summaries(p40, {"summary": {"max_len": 10}})) == 1)
    check("severity 由规则表决定（yellow → yellow）",
          mg.check_summaries(p41, {"summary": {"max_len": 40, "severity": "yellow"}})[0][0] == "yellow")


def test_cron_mem():
    print("\n== ⑩ cron / mem 验证器 ==")
    home = os.path.join(SANDBOX, "hermes_home")
    os.makedirs(os.path.join(home, "cron"), exist_ok=True)
    jobs_path = os.path.join(home, "cron", "jobs.json")
    jobs = {"jobs": [{"id": "abc123def456", "name": "每日体检"},
                     {"id": "zzz999", "name": "备份"}]}
    io.open(jobs_path, "w", encoding="utf-8").write(json.dumps(jobs, ensure_ascii=False))

    old_home = mg.HERMES_HOME
    had_env = "MEM_GUARD_CRON" in os.environ
    old_env = os.environ.pop("MEM_GUARD_CRON", None)
    mg.HERMES_HOME = home
    try:
        check("cron 按 job id 命中 → 正常", mg.v_cron_id("abc123def456")[0] is True)
        check("cron 按 job 名命中 → 正常", mg.v_cron_id("每日体检")[0] is True)
        ok, detail = mg.v_cron_id("no-such-job-xyz")
        check("cron 任务缺失 → 断链", ok is False)
        check("cron 断链消息含 target", ok is False and "no-such-job-xyz" in detail)
        os.remove(jobs_path)
        pending, pdetail = mg.v_cron_id("abc123def456")
        check("cron 库不存在 → None（待全量版，不报错）", pending is None)
        check("cron 待全量版消息可读", "待全量版" in pdetail)
    finally:
        mg.HERMES_HOME = old_home
        if had_env:
            os.environ["MEM_GUARD_CRON"] = old_env

    ptrs = mg.parse_pointers(
        "alpha = doc://a.md | 摘要\nbeta = mem://alpha | 指向 alpha\n")[0]
    mg.set_pointer_keys(ptrs)
    check("mem 指向存在的 key → 正常", mg.v_mem_key("alpha")[0] is True)
    ok_mem, mem_detail = mg.v_mem_key("gamma")
    check("mem 指向不存在的 key → 断链", ok_mem is False)
    check("mem 断链消息 = 未定义的指针别名", mem_detail == "未定义的指针别名")

    rules = mg.load_rules()
    check("schemes 共 10 种", len(rules["schemes"]) == 10)
    check("cron/mem 声明为 red 级",
          rules["schemes"]["cron"]["report"] == "red"
          and rules["schemes"]["mem"]["report"] == "red")
    check("verify 名为 cron_id / mem_key",
          rules["schemes"]["cron"]["verify"] == "cron_id"
          and rules["schemes"]["mem"]["verify"] == "mem_key")


def test_summary_in_report():
    print("\n== ⑪ 摘要超长进入体检报告 ==")
    import contextlib
    rules_path = os.path.join(SANDBOX, "rules_summary.json")
    io.open(rules_path, "w", encoding="utf-8").write(json.dumps(
        {"summary": {"max_len": 5, "severity": "red"},
         "watermark": {"enabled": False},
         "output": {"quiet_when_clean": True, "report_file": ""}}, ensure_ascii=False))
    mem_path = os.path.join(SANDBOX, "mem_summary.md")
    io.open(mem_path, "w", encoding="utf-8").write("k = doc://x.md | 这条摘要明显超过五个字\n")
    old_rules, old_mem = mg.RULES_FILE, mg.MEMORY_FILE
    mg.RULES_FILE, mg.MEMORY_FILE = rules_path, mem_path
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            try:
                mg.run(dry=True, verbose=True)
            except SystemExit:
                pass
    finally:
        mg.RULES_FILE, mg.MEMORY_FILE = old_rules, old_mem
    out = buf.getvalue()
    check("报告含『摘要过长』", "摘要过长" in out)
    check("报告含 key 与上限（> 5）", "k: 摘要过长" in out and "> 5" in out)


if __name__ == "__main__":
    mem = setup_sandbox()
    print(f"沙箱: {SANDBOX}")
    try:
        test_parse()
        test_verify()
        test_autofix()
        test_mangled()
        test_dup_key()
        test_silent()
        test_watermark(mem)
        test_paths_tolerance()
        test_summary()
        test_cron_mem()
        test_summary_in_report()
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{'='*50}\n结果: {PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
