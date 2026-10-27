# -*- coding: utf-8 -*-
"""
stress_pointer_test.py — 弱模型下限压测（指针纪律能不能扛住上下文塞爆）
=====================================================================
思路：指针纪律只要在**最弱模型**上成立，体系就是可靠的。所以故意制造最坏情况：
  1. 大段无关噪声（历史工具输出/日志）把上下文塞满
  2. 指针条目（`key = scheme://target | 摘要`）藏在噪声**中段**
  3. target 的完整内容也在上下文里，但离指针很远（模拟"内容层"）
  4. 问题必须解析 target 才能答全——摘要里故意不含答案
  5. 判分：回答里是否出现 target 的独有关键词（`--expect`），
     出现 = 真的顺着指针取到了内容；不出现 = 只靠摘要/瞎编

只依赖标准库，OpenAI 兼容 `/chat/completions` 接口。provider 全部走环境变量：
    PP_STRESS_BASE_URL   接口地址，默认 http://127.0.0.1:11434/v1（Ollama 的兼容口）
    PP_STRESS_API_KEY    密钥；为空则不发 Authorization 头（本地模型常态）
    PP_STRESS_MODEL      模型名（必填，除 --plan 外）

用法:
    python stress_pointer_test.py --plan                      # 只打印将要发送的 messages
    python stress_pointer_test.py                             # 用内置夹具跑一次
    python stress_pointer_test.py --noise-chars 24000          # 塞得更满
    python stress_pointer_test.py --expect cold-start,9411     # 自定义判分关键词
    python stress_pointer_test.py --target ./my-doc.md \\
        --expect 3080,headless --question "启动方式有哪些？"     # 换成自己的材料
    python stress_pointer_test.py --pointer "dsh = skill://deepseek-harness | 底座" \\
        --pointer "pp = skill://pulse-path | 指针纪律"          # 多条指针
    python stress_pointer_test.py --selftest                  # 离线自测（本地 mock 端点）

判分（--expect 关键词命中数）:
    命中 ≥ 一半（向上取整，至少 1 个）→ PASS（模型真的取到了 target 内容）
    否则 → FAIL（只靠摘要或凭空作答）
退出码: 0 判分通过 / 1 判分失败 / 2 环境或调用错误（缺模型名、网络失败等）
"""
import argparse
import io
import json
import os
import random
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

try:                       # 跨平台：非 UTF-8 控制台/重定向下不因表情符号崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_TIMEOUT = 180

# ── 内置夹具（通用示例，无任何个人路径/密钥）────────────────────────
# 指针摘要故意只说"细节见技能"，不含答案；target 里才有 9411 / --cold-start / --rollback
FIXTURE_POINTERS = [
    "runbook = skill://deploy-runbook | 部署手册（命令与端口细节见技能本体，摘要不含参数）",
    "oncall = skill://oncall-playbook | 值班手册（告警分级与升级路径见技能本体）",
]
FIXTURE_TARGET = """\
【deploy-runbook 技能内容（skill://deploy-runbook 指向的完整内容）】
冷启动（首次装机/状态目录为空时）：./run.sh --cold-start
健康检查端口：9411（GET /healthz 返回 200 即就绪）
状态目录：var/state（回滚快照也放这里）
回滚：./run.sh --rollback <release-id>（release-id 见 var/state/releases.jsonl）
"""
FIXTURE_QUESTION = ("deploy-runbook 的冷启动命令是什么？健康检查走哪个端口？"
                    "回滚用什么命令？")
FIXTURE_EXPECT = ["cold-start", "9411", "rollback"]

SYSTEM_PROMPT = (
    "你是一个运行在大型项目中的 AI 助手，正在处理一个长期任务的中间阶段。"
    "纪律：需要细节时必须解析指针 `scheme://target` 指向的内容，禁止只凭摘要作答，"
    "禁止编造。回答末尾用一行说明答案来源：摘要 / target 内容。"
)

POINTER_HEADER = "【记忆指针字典（机器解析格式）】"
POINTER_RULE = "规则：当需要 detail 时，必须解析 scheme://target 获取完整内容，禁止只凭摘要回答。"

# 噪声模板：模拟长时间运行的工具输出/日志（确定性生成，不用真随机）
NOISE_TEMPLATES = [
    "INFO [{ts}] worker-{w}: batch processed {n} items, ok={ok} fail={f} retry=0 "
    "latency_avg={l}ms p95={p}ms",
    "DEBUG [{ts}] scheduler-{w}: queue_depth={n} inflight={ok} dropped=0 tick={l}ms",
    "INFO [{ts}] fetch-{w}: GET /api/v1/items?page={n} -> 200 bytes={ok}000 in {l}ms",
    "WARN [{ts}] gc-{w}: heap_used={n}MB threshold=512MB compacted={ok} objs",
    "INFO [{ts}] index-{w}: rebuilt shard {n}/{ok} docs={l}000 elapsed={p}ms",
]


def make_noise(chars, seed=7):
    """造 chars 个字符的无关日志噪声（固定种子 → 每次运行完全一致，便于复现）。"""
    if chars <= 0:
        return ""
    rnd = random.Random(seed)
    out, size, i = [], 0, 0
    while size < chars:
        tpl = NOISE_TEMPLATES[i % len(NOISE_TEMPLATES)]
        line = tpl.format(
            ts="2026-01-01 %02d:%02d:%02d" % (i % 24, (i * 7) % 60, (i * 13) % 60),
            w=rnd.randint(1, 16), n=rnd.randint(8, 999), ok=rnd.randint(50, 999),
            f=rnd.randint(0, 3), l=rnd.randint(3, 180), p=rnd.randint(20, 900))
        out.append(line)
        size += len(line) + 1
        i += 1
    return ("\n".join(out) + "\n")[:chars]


def build_pointer_block(pointers):
    """指针块：字典头 + 若干 `key = scheme://target | 摘要` + 解析规则。"""
    lines = [POINTER_HEADER] + list(pointers) + ["", POINTER_RULE]
    return "\n".join(lines) + "\n"


def build_messages(noise_chars=12000, pointers=None, target_body=None,
                   question=None, seed=7):
    """组装 messages：噪声 55% → 指针块 → 噪声 30% → target 内容 → 噪声 15% → 问题。

    指针块落在中段、target 落在靠后，两者之间隔着噪声——这是"必须顺着指针找内容"
    的最小可复现结构。
    """
    pointers = list(FIXTURE_POINTERS if pointers is None else pointers)
    target_body = FIXTURE_TARGET if target_body is None else target_body
    question = FIXTURE_QUESTION if question is None else question

    total = max(0, int(noise_chars))
    n_head = int(total * 0.55)
    n_mid = int(total * 0.30)
    n_tail = total - n_head - n_mid
    if total and n_head == total:               # 极小噪声也保证 target 之后有尾段
        n_head -= 1
        n_tail = 1

    user = (
        "以下是你的会话上下文（含历史工具输出，很长）：\n\n"
        "[历史上下文开始]\n"
        + make_noise(n_head, seed) +
        build_pointer_block(pointers) + "\n"
        "[历史上下文继续]\n"
        + make_noise(n_mid, seed + 1) + "\n"
        "[文档内容片段（指针 target 指向的内容层）]\n"
        + target_body + "\n"
        + make_noise(n_tail, seed + 2) +
        "[历史上下文结束]\n\n"
        "【用户问题】" + question + "\n"
        "注意：答案不在摘要里，在指针 target 指向的内容中。\n"
        "请回答，并用一行说明答案来源（摘要 or target 内容）。"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


# ── 判分 ──────────────────────────────────────────────────────────────
def judge(answer, expect, pass_at=None):
    """关键词判分：命中数 ≥ 阈值即通过（默认一半，向上取整，至少 1 个）。"""
    answer_l = (answer or "").lower()
    expect = [k for k in (expect or []) if k]
    hits = [k for k in expect if k.lower() in answer_l]
    misses = [k for k in expect if k not in hits]
    need = pass_at if pass_at is not None else max(1, (len(expect) + 1) // 2)
    ok = bool(expect) and len(hits) >= need
    return {"pass": ok, "hits": hits, "misses": misses, "total": len(expect), "need": need}


# ── 接口调用 ──────────────────────────────────────────────────────────
def build_endpoint(base_url):
    """把 base_url 规范成 /chat/completions 端点（兼容三种常见写法）。"""
    base_url = (base_url or "").strip()
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("base_url 必须是 http(s)://host[:port][/path] 形式: %r" % base_url)
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    if not path:
        path = "/v1/chat/completions"           # OpenAI 默认版本前缀
    else:
        path = path + "/chat/completions"       # /v1、/api/paas/v4 等自带前缀
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def call_model(base_url, api_key, model, messages, timeout=DEFAULT_TIMEOUT,
               temperature=0.0, max_tokens=None):
    """POST /chat/completions，返回 (回答文本, 原始响应 dict)。失败抛 RuntimeError。"""
    url = build_endpoint(base_url)
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "stream": False}
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError("HTTP %s %s: %s" % (e.code, e.reason, detail))
    except urllib.error.URLError as e:
        raise RuntimeError("连接失败（%s）：%s" % (url, e.reason))
    try:
        data = json.loads(body)
    except ValueError:
        raise RuntimeError("响应不是 JSON（前 200 字）：%s" % body[:200])
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError("接口返回错误：%s" % json.dumps(data["error"], ensure_ascii=False)[:300])
    try:
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        content = msg.get("content", choice.get("text", ""))
        if isinstance(content, list):          # 多段内容（部分兼容实现）
            content = "".join(p.get("text", "") if isinstance(p, dict) else str(p)
                             for p in content)
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("响应缺少 choices[0].message.content（前 200 字）：%s" % body[:200])
    return content or "", data


# ── 人读输出 ──────────────────────────────────────────────────────────
def _window(text, head=200, tail=200):
    if len(text) <= head + tail:
        return text
    return (text[:head] + "\n...（省略 %d 字符）...\n" % (len(text) - head - tail)
            + text[-tail:])


def print_plan(base_url, model, messages, fixture):
    total = sum(len(m["content"]) for m in messages)
    print("=== 压测计划（--plan：不联网）===")
    print("端点: %s" % build_endpoint(base_url))
    print("模型: %s" % (model or "(未设置 PP_STRESS_MODEL)"))
    print("messages: %d 条，总字符 %d" % (len(messages), total))
    for i, m in enumerate(messages):
        print("\n--- [%d] %s（%d 字符）---" % (i + 1, m["role"], len(m["content"])))
        if m["role"] == "user":
            print(_window(m["content"], 300, 300))
        else:
            print(m["content"])
    user = messages[-1]["content"]
    for label, needle in (("指针块", fixture["pointer_first"]),
                          ("target 内容", fixture["target_marker"])):
        pos = user.find(needle)
        pct = (100.0 * pos / len(user)) if pos >= 0 and user else -1
        print("%s位置: %s（%.0f%%）" % (label, pos if pos >= 0 else "未找到", pct))
    print("\n判分关键词: %s（需命中 %d 个）"
          % (", ".join(fixture["expect"]), max(1, (len(fixture["expect"]) + 1) // 2)))


def print_result(verdict, model, noise_chars, answer, elapsed):
    print("=== 压测结果 ===")
    print("模型: %s   噪声: %d 字符   耗时: %.1fs" % (model, noise_chars, elapsed))
    print("判定: %s（命中 %d/%d，需 %d）" % (
        "✅ PASS 解析到了 target" if verdict["pass"] else "❌ FAIL 只靠摘要/瞎编",
        len(verdict["hits"]), verdict["total"], verdict["need"]))
    print("命中: %s" % (", ".join(verdict["hits"]) or "(无)"))
    if verdict["misses"]:
        print("缺失: %s" % ", ".join(verdict["misses"]))
    print("回答片段: %s" % (answer or "(空)")[:300].replace("\n", " "))


# ── 自测 ──────────────────────────────────────────────────────────────
def selftest():
    """离线自测：本地 mock 端点验证请求构造 + 判分，不需要真 key/外网。"""
    import contextlib
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        print("  %s %s" % ("✅" if cond else "❌", name))

    captured = {"requests": []}
    answer_with_hits = "冷启动用 ./run.sh --cold-start，健康检查端口 9411，回滚用 ./run.sh --rollback <release-id>。来源：target 内容"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"_bad_json": raw[:200]}
            captured["requests"].append({"path": self.path,
                                         "headers": {k.lower(): v for k, v in self.headers.items()},
                                         "payload": payload})
            if self.path != "/v1/chat/completions":
                body = b'{"error":"no such endpoint"}'
                self.send_response(404)
            else:
                body = json.dumps({"choices": [{"message": {"role": "assistant",
                                                             "content": answer_with_hits}}]}
                                  ).encode("utf-8")
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    base = "http://127.0.0.1:%d" % port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # ① 端点规范化
        check("端点: 裸 host 补 /v1/chat/completions",
              build_endpoint("http://h:1234") == "http://h:1234/v1/chat/completions")
        check("端点: /v1 前缀补 /chat/completions",
              build_endpoint("http://h:1234/v1") == "http://h:1234/v1/chat/completions")
        check("端点: 第三方前缀（/api/paas/v4）保留",
              build_endpoint("https://x.y/api/paas/v4") == "https://x.y/api/paas/v4/chat/completions")
        check("端点: 已是完整端点不重复拼",
              build_endpoint("http://h/v1/chat/completions") == "http://h/v1/chat/completions")
        try:
            build_endpoint("127.0.0.1:11434")
            bad_scheme = False
        except ValueError:
            bad_scheme = True
        check("端点: 非法/缺协议被拒", bad_scheme)

        # ② messages 结构：指针在中段、target 靠后、问题在末尾
        msgs = build_messages(12000)
        check("messages = system + user", [m["role"] for m in msgs] == ["system", "user"])
        user = msgs[-1]["content"]
        ppos = user.find("runbook = skill://deploy-runbook")
        tpos = user.find("冷启动（首次装机")
        check("指针块落在噪声中段（35%~75%）",
              0.35 <= ppos / len(user) <= 0.75)
        check("target 内容在指针之后且靠后（>70%）", tpos > ppos and tpos / len(user) > 0.70)
        check("问题在末尾", user.rstrip().endswith("（摘要 or target 内容）。"))
        check("摘要里不含答案关键词",
              not any(k in user[:ppos] + user[ppos:tpos] for k in FIXTURE_EXPECT))
        check("噪声规模受 --noise-chars 控制（±10%）",
              abs(len(make_noise(12000)) - 12000) <= 1200)
        check("--noise-chars 0 也能构造", len(build_messages(0)[1]["content"]) > 0)
        check("噪声确定性（同种子同输出）", make_noise(2000) == make_noise(2000))
        check("自定义指针覆盖内置夹具",
              "pp = skill://pulse-path" in build_messages(500, pointers=["pp = skill://pulse-path | 指针纪律"])[1]["content"])

        # ③ 判分
        v_all = judge(answer_with_hits, FIXTURE_EXPECT)
        check("判分: 全命中通过", v_all["pass"] and len(v_all["hits"]) == 3)
        check("判分: 关键词大小写不敏感", judge("用 --COLD-START 启动", ["cold-start"])["pass"])
        v_none = judge("启动脚本在 skills 目录里，自己看下。", FIXTURE_EXPECT)
        check("判分: 零命中失败", not v_none["pass"] and v_none["misses"] == FIXTURE_EXPECT)
        check("判分: 阈值=一半向上取整（2/3 通过）",
              judge("--cold-start 与 9411", FIXTURE_EXPECT)["pass"]
              and judge("只有 9411", FIXTURE_EXPECT)["need"] == 2)
        check("判分: 单关键词需命中 1 个",
              judge("port 9411", ["9411"])["pass"] and judge("nope", ["9411"])["need"] == 1)

        # ④ 请求构造（真实发一次 HTTP 到本地 mock）
        answer, raw = call_model(base, "test-key-123", "demo-model", msgs, timeout=20)
        req = captured["requests"][-1]
        check("请求打到 /v1/chat/completions", req["path"] == "/v1/chat/completions")
        check("模型名进 payload", req["payload"].get("model") == "demo-model")
        check("messages 原样发送（system+user）",
              [m.get("role") for m in req["payload"].get("messages", [])] == ["system", "user"])
        check("stream=False（一次性响应）", req["payload"].get("stream") is False)
        check("带 key 时发 Bearer 头",
              req["headers"].get("authorization") == "Bearer test-key-123")
        check("Content-Type 为 application/json",
              req["headers"].get("content-type") == "application/json")
        check("响应解析出回答文本", "cold-start" in answer)
        check("原始响应含 choices", isinstance(raw, dict) and raw.get("choices"))

        # 无 key 不应发 Authorization 头
        call_model(base, "", "demo-model", [{"role": "user", "content": "hi"}], timeout=20)
        check("无 key 时不发 Authorization 头",
              "authorization" not in captured["requests"][-1]["headers"])

        # ⑤ main 全流程（--plan 不联网 / 真跑 / 错误路径）
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc_plan = main(["--plan", "--url", "http://127.0.0.1:9", "--model", "m"])
        plan_text = out.getvalue()
        check("--plan 不联网且退出 0", rc_plan == 0 and "压测计划" in plan_text)
        check("--plan 打印指针位置与判分关键词",
              "指针块位置" in plan_text and "9411" in plan_text)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc_run = main(["--url", base, "--model", "demo-model", "--noise-chars", "800",
                           "--expect", ",".join(FIXTURE_EXPECT)])
        check("真跑一次：命中 → 退出 0", rc_run == 0, )
        check("真跑输出 PASS 与命中数", "PASS" in out.getvalue() and "命中 3/3" in out.getvalue())

        # 自定义 --expect 不命中 → 退出 1
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            rc_miss = main(["--url", base, "--model", "demo-model", "--noise-chars", "300",
                            "--expect", "zzz-not-present"])
        check("判分失败 → 退出 1", rc_miss == 1 and "FAIL" in out2.getvalue())

        # --json 输出可解析
        out3 = io.StringIO()
        with contextlib.redirect_stdout(out3):
            rc_json = main(["--url", base, "--model", "demo-model", "--noise-chars", "300",
                            "--json", "--expect", "9411"])
        try:
            payload = json.loads(out3.getvalue())
        except ValueError:
            payload = None
        check("--json 输出可解析且含 verdict/hits",
              rc_json == 0 and isinstance(payload, dict)
              and payload.get("verdict", {}).get("hits") == ["9411"])

        # 错误路径：错误端点 → 退出 2；缺模型名 → 退出 2
        out4 = io.StringIO()
        with contextlib.redirect_stdout(out4):
            rc_bad = main(["--url", base + "/wrong", "--model", "demo-model",
                           "--noise-chars", "100"])
        check("HTTP 404 → 退出 2 并给错误信息",
              rc_bad == 2 and "调用失败" in out4.getvalue())

        old_model = os.environ.pop("PP_STRESS_MODEL", None)
        out5 = io.StringIO()
        with contextlib.redirect_stdout(out5):
            rc_nomodel = main(["--url", base, "--noise-chars", "100"])
        if old_model is not None:
            os.environ["PP_STRESS_MODEL"] = old_model
        check("缺模型名 → 退出 2 并提示 PP_STRESS_MODEL",
              rc_nomodel == 2 and "PP_STRESS_MODEL" in out5.getvalue())
        # 自定义 --target 但不给 --expect → 直接拒绝（防止用内置关键词误判）
        tmp_target = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "_selftest_target.tmp")
        try:
            with io.open(tmp_target, "w", encoding="utf-8", newline="\n") as f:
                f.write("自定义 target 正文\n")
            out6 = io.StringIO()
            with contextlib.redirect_stdout(out6):
                rc_texp = main(["--url", base, "--model", "demo-model",
                                "--target", tmp_target, "--noise-chars", "100"])
            check("--target 缺 --expect → 退出 2 并提示",
                  rc_texp == 2 and "--expect" in out6.getvalue())
        finally:
            try:
                os.unlink(tmp_target)
            except OSError:
                pass
    finally:
        server.shutdown()
        server.server_close()

    ok = sum(1 for _, c in checks if c)
    print("\nSELFTEST %s %d/%d" % ("OK" if ok == len(checks) else "FAIL", ok, len(checks)))
    return 0 if ok == len(checks) else 1


# ── CLI ───────────────────────────────────────────────────────────────
USAGE = """stress_pointer_test — 弱模型下限压测（塞爆上下文，看指针纪律还在不在）

用法:
  python stress_pointer_test.py [选项]

选项:
  --noise-chars N   无关噪声字符数（默认 12000；越大越接近塞爆）
  --pointer TEXT    指针条目行，可重复（`key = scheme://target | 摘要`）
  --target PATH     target 正文文件（默认用内置夹具；配 --expect 使用）
  --question TEXT   问题（默认内置；答案必须在 target 里，摘要里没有）
  --expect a,b,c    判分关键词，逗号分隔（命中一半即 PASS）
  --plan            只打印将发送的 messages 与位置信息，不联网
  --json            机器可读结果（与 --plan 同用则输出 messages）
  --url URL         覆盖 PP_STRESS_BASE_URL
  --model NAME      覆盖 PP_STRESS_MODEL
  --timeout SEC     请求超时（默认 180）
  --temperature T   采样温度（默认 0，压测要可复现）
  --selftest        离线自测（本地 mock 端点，无需 key/外网）
  --help            本帮助

环境变量:
  PP_STRESS_BASE_URL  默认 http://127.0.0.1:11434/v1（Ollama 兼容口）
  PP_STRESS_API_KEY   为空则不发 Authorization 头
  PP_STRESS_MODEL     模型名（除 --plan 外必填）

退出码: 0 通过 / 1 判分失败 / 2 环境或调用错误
"""


def parse_args(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--noise-chars", type=int, default=12000)
    ap.add_argument("--pointer", action="append", default=[])
    ap.add_argument("--target", default=None)
    ap.add_argument("--question", default=None)
    ap.add_argument("--expect", default=None)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--url", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--temperature", type=float, default=0.0)
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

    base_url = args.url or os.environ.get("PP_STRESS_BASE_URL") or DEFAULT_BASE_URL
    api_key = os.environ.get("PP_STRESS_API_KEY") or ""
    model = args.model or os.environ.get("PP_STRESS_MODEL") or ""

    pointers = args.pointer or list(FIXTURE_POINTERS)
    target_body = FIXTURE_TARGET
    if args.target:
        try:
            target_body = io.open(args.target, encoding="utf-8", errors="replace").read()
        except OSError as e:
            print("读取 --target 失败: %s" % e)
            return 2
    question = args.question or FIXTURE_QUESTION
    if args.target and not args.expect:
        print("用了 --target 就要配 --expect 指定判分关键词（内置关键词对应内置夹具）。")
        return 2
    if args.expect:
        expect = [k.strip() for k in args.expect.split(",") if k.strip()]
    else:
        expect = list(FIXTURE_EXPECT)

    messages = build_messages(args.noise_chars, pointers, target_body, question)
    fixture = {"pointer_first": pointers[0], "target_marker": target_body.strip().split("\n")[0],
               "expect": expect}

    if args.plan:
        if args.json:
            print(json.dumps({"endpoint": build_endpoint(base_url), "model": model,
                              "messages": messages, "expect": expect},
                             ensure_ascii=False, indent=2))
        else:
            print_plan(base_url, model, messages, fixture)
        return 0

    if not model:
        print("缺少模型名：设置 PP_STRESS_MODEL 或用 --model 指定（见 --help）。")
        return 2

    started = time.time()
    try:
        answer, _raw = call_model(base_url, api_key, model, messages,
                                  timeout=args.timeout, temperature=args.temperature)
    except Exception as e:
        print("调用失败: %s" % e)
        return 2
    elapsed = time.time() - started
    verdict = judge(answer, expect)

    if args.json:
        print(json.dumps({"model": model, "noise_chars": args.noise_chars,
                          "verdict": verdict, "elapsed_s": round(elapsed, 2),
                          "answer": answer}, ensure_ascii=False, indent=2))
    else:
        print_result(verdict, model, args.noise_chars, answer, elapsed)
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
