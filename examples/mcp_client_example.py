#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""examples/mcp_client_example.py — 零依赖 MCP stdio 客户端示范（只用标准库）
与外部 MCP 记忆服务对话：initialize → tools/list → 一次 tools/call；分帧同
mcp_server/pulse_path_mcp.py（换行分隔的 JSON-RPC，一行一条消息）。
--probe 给 mem_guard 的 store:// 反查用：命中 0 / 服务答未找到 64 / 连不上 65。
不给 --command 就打印用法并 exit 2；--selftest 拿本仓 MCP 服务离线自检：
    python examples/mcp_client_example.py --command "<服务启动命令>" --tool memory_get --args '{"id":"{target}"}' --probe note-42
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import threading

SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp_server", "pulse_path_mcp.py")
OK, MISSING, UNAVAILABLE = 0, 64, 65
MISS = ("not found", "not_found", "no such", "missing", "未找到", "不存在")


class Session(object):
    """一条 stdio JSON-RPC 会话：spawn 服务 → 发帧 → 收帧（按 id 对上）。"""
    def __init__(self, argv, timeout=20.0):
        self.timeout, self.seq, self.box = timeout, 0, queue.Queue()
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=lambda: ([self.box.put(x) for x in self.proc.stdout], self.box.put(None)),
                         daemon=True).start()
    def send(self, msg):
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False).encode() + b"\n")
        self.proc.stdin.flush()
    def request(self, method, params=None):
        self.seq += 1
        self.send({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params or {}})
        while True:
            line = self.box.get(timeout=self.timeout)      # 超时抛 queue.Empty，调用方转 65
            if line is None: raise RuntimeError("%s 无响应：服务已关闭 stdout" % method)
            reply = json.loads(line.decode("utf-8", "replace") or "{}")
            if reply.get("id") == self.seq:
                return reply
    def close(self):
        try: self.proc.stdin.close(); self.proc.wait(timeout=5)    # 关 stdin 让服务正常退出
        except Exception: self.proc.kill()

def handshake(sess):
    """initialize → notifications/initialized → tools/list，返回 (serverInfo, [工具名])。"""
    init = sess.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "pulse-path-example"}})
    if "error" in init: raise RuntimeError("initialize 失败: %s" % init["error"])
    sess.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    tools = sess.request("tools/list").get("result", {}).get("tools") or []
    return init["result"].get("serverInfo", {}), [t.get("name") for t in tools]

def exit_verdict(reply):
    """tools/call 回复 → 0 命中 / 64 未找到（含空结果）/ 65 服务不可用，与 store.exit_map 对齐。"""
    if "error" in reply:                       # 协议/服务层报错，不指控指针
        return UNAVAILABLE, "JSON-RPC 错误: %s" % reply["error"]
    res = reply.get("result") or {}
    sc = res.get("structuredContent") if isinstance(res.get("structuredContent"), dict) else {}
    text = (json.dumps(sc, ensure_ascii=False) + " ".join(c.get("text", "") for c in (res.get("content") or []) if isinstance(c, dict))).strip()
    if sc.get("found") is False or not text or any(m in text.lower() for m in MISS):
        return MISSING, "外部服务答未找到"
    return (UNAVAILABLE, "工具报错: %s" % text[:160]) if res.get("isError") else (OK, text[:200])

def run(cmd, tool, args_json, target, timeout, field="target"):
    """连一次服务：initialize → tools/list → tools/call，返回 (退出码, 说明)。
    target=None 打印清单与调用结果；target 非空只判存在性（--args 写 {target} 就替换，否则按 field 命名）。"""
    sess = None
    try:
        sess = Session(cmd, timeout)
        info, tools = handshake(sess)
        if target is None:
            print("服务: %s\n工具: %s" % (info.get("name"), ", ".join(tools) or "（无）"))
            if tool: print(json.dumps(sess.request("tools/call", {"name": tool, "arguments": json.loads(args_json)}), ensure_ascii=False, indent=1))
            return OK, "服务 %s 可用，工具: %s" % (info.get("name"), ", ".join(tools))
        esc = json.dumps(target, ensure_ascii=False)[1:-1]     # 目标值的转义形式，塞进 JSON 字符串才安全
        args = (json.loads(args_json.replace("{target}", esc)) if "{target}" in args_json
                else dict(json.loads(args_json or "{}"), **{field: target}))
        return exit_verdict(sess.request("tools/call", {"name": tool, "arguments": args}))
    except (RuntimeError, ValueError, OSError, queue.Empty) as e:
        return UNAVAILABLE, "连不上或会话失败: %s" % e
    finally:
        sess and sess.close()

def selftest():
    """拿本仓自己的 MCP 服务当被测服务：全链路 + 三种退出码，离线可跑。"""
    cmd = [sys.executable, SERVER]
    code, detail = run(cmd, "", "{}", None, 60)            # 全链路：initialize → tools/list
    cases = [("initialize + tools/list 握手", code == OK and "pp_secrets_scan" in detail)]
    cases += [("--probe README.md → exit 0", run(cmd, "pp_secrets_scan", "{}", "README.md", 60, "path")[0] == OK),
              ("--probe 不存在 → exit 64", run(cmd, "pp_secrets_scan", "{}", "no-such-xyz.md", 60, "path")[0] == MISSING),
              ("--args 写 {target} 也行", run(cmd, "pp_secrets_scan", '{"path":"{target}"}', "README.md", 60)[0] == OK)]
    cases.append(("--probe 服务起不来 → exit 65", run(["not-a-real-mcp-server-xyz"], "t", "{}", "t", 5)[0] == UNAVAILABLE))
    for name, cond in cases: print("  %s %s" % ("✅" if cond else "❌", name))
    good = [c for _, c in cases]
    print("\n%s %d/%d" % ("SELFTEST OK" if all(good) else "SELFTEST FAIL", sum(good), len(good)))
    return 0 if all(good) else 1

def main(argv=None):
    ap = argparse.ArgumentParser(description="零依赖 MCP stdio 客户端示例（默认不连任何服务）")
    ap.add_argument("--command", default="", help='要 spawn 的服务命令，按空格切，例："npx -y some-mcp-server"')
    ap.add_argument("--tool", default="", help="tools/call 的工具名")
    ap.add_argument("--args", default="{}", help='tools/call 参数 JSON；写 {target} 则原位替换')
    ap.add_argument("--field", default="target", help='--args 里放 target 的字段名（默认 target）')
    ap.add_argument("--probe", default="", help="校验模式：命中 exit 0 / 未找到 64 / 连不上 65")
    ap.add_argument("--timeout", type=float, default=20.0, help="单条消息等待秒数（默认 20）")
    ap.add_argument("--selftest", action="store_true", help="拿本仓 MCP 服务自检")
    a = ap.parse_args(argv)
    if a.selftest: return selftest()
    if not a.command:                          # 本示例默认不连任何服务
        print(ap.format_help() + "\n必须显式给 --command"); return 2
    code, detail = run(a.command.split(), a.tool, a.args, a.probe or None, a.timeout, a.field)  # 命令按空格切
    if a.probe: print("%s → exit %d  %s" % (a.probe, code, detail))
    elif code != OK: print(detail)
    return code

if __name__ == "__main__":
    sys.exit(main())
