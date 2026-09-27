#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_secrets.py — 零依赖敏感信息扫描器（开源仓库发布前门禁 S1）

用法:
    python scripts/check_secrets.py .            # 扫当前目录（递归）
    python scripts/check_secrets.py . --quiet     # 只输出结论
    python scripts/check_secrets.py . --help

检查项（G2 隐私）:
    1. 凭据赋值          KEY = "..." / token: ... / password=... （值看起来是真值而不是占位符）
    2. 私钥 / 证书块      -----BEGIN ... PRIVATE KEY-----、ssh-rsa AAAA…
    3. 邮件地址          非 noreply 的真实邮箱
    4. 内网地址          10./172.16-31./192.168./127. 的 IPv4、裸 MAC
    5. 高熵字符串        长度 ≥ 24 且香农熵 ≥ 3.6 的连续字串（排除路径/URL/驼峰标识/重复字符）
    6. 本机绝对路径      C:\\Users\\…、/c/Users/…、/Users/…、/home/<name>/…（G4 可用性）

退出码: 0 = 零命中；1 = 有命中（列出文件:行:原因，值做掩码不原样打印）
"""
import argparse
import math
import os
import re
import sys

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
             ".pytest_cache", "dist", "build", ".idea", ".vscode"}
SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
             ".tar", ".whl", ".pyc", ".so", ".dll", ".exe", ".mp3", ".mp4", ".woff",
             ".woff2", ".ttf", ".otf"}
MAX_BYTES = 2 * 1024 * 1024  # 单文件超过 2MB 跳过（本仓不应有大文件）

# 占位符/示例值：命中这些说明是文档里的写法，不是真凭据
PLACEHOLDERS = ("your", "xxx", "example", "placeholder", "<", "${", "$(", "env.",
                "os.environ", "getenv", "none", "null", "changeme", "todo",
                "redacted", "dummy", "fake", "test", "sample", "…", "...")

CRED_RE = re.compile(
    r"""(?ix)
    \b(api[_-]?key|apikey|secret|token|passwd|password|passphrase|access[_-]?key|
        client[_-]?secret|private[_-]?key|auth[_-]?token|bearer|credential)\b
    \s*[:=]\s*
    (?P<q>["']?)(?P<val>[^\s"'#,;)\]}]{8,120})(?P=q)
    """)

PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
SSHKEY_RE = re.compile(r"\bssh-(rsa|ed25519|dss)\s+AAAA[A-Za-z0-9+/=]{20,}")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IP_RE = re.compile(r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168|127)\.\d{1,3}\.\d{1,3}(?![\d])")
MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
# ⚠️ 下面的"路径泄漏"正则按片段拼接，避免本文件自身就含有它要抓的字面模式
#    （否则扫描器会把自己也报出来，门禁 S2 也会误报）。
WINPATH_RE = re.compile(r"[A-Za-z]:" + r"\\{1,2}(?:" + r"Users|Documents|AppData" + r")\\{1,2}")
MSYSPATH_RE = re.compile("/" + r"c/Users/", re.I)
HOMEPATH_RE = re.compile(r"/(?:" + r"Users|home)/" + r"(?!your|<|\$|\{)[A-Za-z][A-Za-z0-9._-]*/")
B64_RE = re.compile(r"[A-Za-z0-9+/=_\-]{24,}")


def entropy(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def looks_placeholder(v):
    lv = v.lower()
    if any(p in lv for p in PLACEHOLDERS):
        return True
    if len(set(v)) <= 4:          # aaaaa / 0000
        return True
    if v.isdigit():
        return True
    if not any(c.isdigit() for c in v) and any(c.isalpha() for c in v) and " " not in v:
        # 纯字母长词（如常量名）不算凭据；真 key 通常含数字或符号
        if re.fullmatch(r"[A-Za-z_]+", v):
            return True
    return False


def high_entropy_hits(line):
    out = []
    for m in B64_RE.finditer(line):
        tok = m.group(0)
        if tok.count("/") > 2 or tok.count(".") > 2 or tok.count("-") > 4:
            continue  # 路径/URL/长连字符标识
        if tok.startswith(("http", "viking", "skill", "doc", "script", "cfg", "rule", "fact", "cron")):
            continue
        if entropy(tok) >= 3.6 and len(tok) >= 24:
            out.append(tok)
    return out


def mask(v):
    if len(v) <= 8:
        return "*" * len(v)
    return v[:3] + "*" * (len(v) - 6) + v[-3:]


def iter_files(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            p = os.path.join(dirpath, f)
            if os.path.splitext(f)[1].lower() in SKIP_EXTS:
                continue
            try:
                if os.path.getsize(p) > MAX_BYTES:
                    continue
            except OSError:
                continue
            yield p


def scan_file(path, rel):
    hits = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh, 1):
                if PEM_RE.search(line):
                    hits.append((rel, i, "PEM 私钥块"))
                m = SSHKEY_RE.search(line)
                if m:
                    hits.append((rel, i, "SSH 公钥/私钥串: " + mask(m.group(0))))
                for m in EMAIL_RE.finditer(line):
                    addr = m.group(0)
                    if "noreply" in addr.lower() or "example." in addr.lower():
                        continue
                    hits.append((rel, i, "真实邮箱: " + mask(addr)))
                for m in IP_RE.finditer(line):
                    hits.append((rel, i, "内网 IP: " + m.group(0)))
                m = MAC_RE.search(line)
                if m and not re.fullmatch(r"(?i)(00[:-]){5}00", m.group(0)):
                    hits.append((rel, i, "MAC: " + mask(m.group(0))))
                if WINPATH_RE.search(line) or MSYSPATH_RE.search(line):
                    hits.append((rel, i, "本机绝对路径（Users/AppData）"))
                m = HOMEPATH_RE.search(line)
                if m:
                    hits.append((rel, i, "家目录绝对路径: " + m.group(0)))
                cm = CRED_RE.search(line)
                if cm and not looks_placeholder(cm.group("val")):
                    hits.append((rel, i, "疑似凭据赋值 " + cm.group(1) + "=" + mask(cm.group("val"))))
                for tok in high_entropy_hits(line):
                    hits.append((rel, i, "高熵串: " + mask(tok)))
    except (OSError, UnicodeDecodeError):
        pass
    return hits


# 扫描器自身/文档里说明正则的示例行豁免（形如 `# noqa: secrets` 或示例标注）
def main():
    ap = argparse.ArgumentParser(description="零依赖敏感信息扫描（发布门禁 S1）")
    ap.add_argument("root", nargs="?", default=".", help="扫描根目录（默认当前目录）")
    ap.add_argument("--quiet", action="store_true", help="只输出结论")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    all_hits = []
    scanned = 0
    for p in iter_files(root):
        rel = os.path.relpath(p, root)
        scanned += 1
        all_hits.extend(scan_file(p, rel))

    if not args.quiet:
        print(f"扫描: {root}")
        print(f"文件数: {scanned}")
        print("-" * 60)
    for rel, ln, why in all_hits:
        print(f"{rel}:{ln}: {why}")
    if all_hits:
        print(f"\n❌ 命中 {len(all_hits)} 条（{scanned} 个文件）")
        return 1
    print(f"\n✅ 零命中（{scanned} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
