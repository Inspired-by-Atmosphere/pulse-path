#!/usr/bin/env python3
"""check_secrets.py - zero-dependency leak scanner for a pre-publish gate.

Run this over a repository (or a single file) before it becomes public. The
gate passes only when the report says CLEAN.

Usage:
    python scripts/check_secrets.py .
    python scripts/check_secrets.py . --exclude-path examples/fixtures
    python scripts/check_secrets.py examples/fixtures/leaky_sample.env
    python scripts/check_secrets.py . --show-info
    python scripts/check_secrets.py . --json

What it reports (any finding makes the exit code 1):
  1. Credential-shaped strings: cloud access keys, private key blocks, GitHub /
     Slack / Google / OpenAI-style tokens, bearer tokens, JWTs, and
     `keyword = value` assignments that carry a literal value.
  2. High-entropy tokens that match no known prefix.
  3. Identity and topology leaks: e-mail addresses, RFC1918 / CGNAT IPv4
     addresses, MAC addresses, Windows drive paths and MSYS/WSL home paths,
     `.internal`-style host names.
  4. Account-identifier literals (`qq`, `uid`, `openid`, `session_id`).
  5. Documentation placeholders that were never filled in (`<PLACEHOLDER>`,
     TODO, XXX, `your-...`) - reported as INFO, never as a failure.

The scanner never prints a full match: every finding is masked (the first four
characters are kept). A report is therefore safe to paste into a ticket or a
chat without leaking the value it is about. `absolute-path` findings print no
excerpt at all, so a pasted report cannot contain a drive-letter path that
would then trip a path grep.

Allowed by default, so a documentation-rich repo stays clean:
  * placeholder values after a keyword: `$VAR`, `${VAR}`, `<PLACEHOLDER>`,
    `%VAR%`, `your-...`, `example`, `xxx`, `redacted`, `placeholder`, `none`,
    `null`, `true`, `false`, `os.environ`, `getenv`
  * documentation address forms: `192.168.1.x`, `10.0.0.x` (the `x` octet is
    read as documentation, not as an address)
  * the canonical placeholder MAC `AA:BB:CC:DD:EE:FF` (any case)
  * e-mail addresses ending in `@example.com|org|net`, `@localhost`,
    `@invalid`, `@users.noreply.github.com`, `@noreply...`
  * paths that carry a placeholder token (`<...>`, `$VAR`, `%VAR%`) or a
    generic segment such as `path`, `to`, `repo`, `src`, `tmp`, `out`
  * a regex character class inside a path-shaped string (the "/c/" plus
    "[A-Za-z]" form that gate documentation uses when it quotes a rule)
  * stock OpenWrt UCI section names (`dhcp.lan`, `interface.lan`, ...) which
    match the `.lan` host-name shape but exist in every vanilla installation

Exit codes: 0 = clean, 1 = findings, 2 = usage error.
Standard library only. No network access, nothing is uploaded.
"""

import argparse
import json
import os
import re
import sys

SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
    ".pytest_cache", "dist", "build",
}
SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".7z", ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".woff", ".woff2",
    ".ttf", ".mp4",
}

# Sensitive literals are assembled from fragments so that this file never
# matches its own rules. A scanner that trips on itself is useless.
_F = lambda *parts: "".join(parts)

# --- rules ----------------------------------------------------------------

# Windows drive paths ("C:" + separator) and MSYS/WSL user paths (the "/c/"
# and "/mnt/" prefix forms). A match is reported unless DOC_PATH_RE finds a
# placeholder or a generic documentation segment inside it.
ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|(?<![A-Za-z0-9:/])/(?:[c-z]|mnt/[c-z])/)"
    r"[^\s\"'`|;,)<>\]]+")

# Placeholder / documentation shapes that make an absolute path harmless.
DOC_PATH_RE = re.compile(
    r"(?i)(?:<[^<>]*>|\$[A-Za-z_{(]|%[A-Za-z_]+%|"
    r"(?:^|[\\/])(?:path|paths|to|your|yours|example|sample|demo|some|repo|repos|"
    r"project|projects|workspace|work|src|source|code|tmp|temp|data|out|output|"
    r"build|dist|dir|dirs|folder|folders|file|files|name|names|root|opt|var|"
    r"etc|usr|share)(?:[\\/]|$))")

# A regex character class inside a path-shaped string (the "/c/" + "[A-Za-z]"
# form used in gate documentation) is a pattern, not a path.
CLASS_IN_PATH_RE = re.compile(r"\[[^\]\s]{1,32}\]?")

# An elided path ("/c/...", "C:\\...", "<drive>:/path/to/x.py") is documentation
# shorthand. Only the dots survive once the prefix is stripped, so nothing about
# the author's real filesystem is disclosed.
ELIDED_PATH_RE = re.compile(r"(?i)^(?:[A-Za-z]:[\\/]|/(?:[c-z]|mnt/[c-z])/)?[.\\/\s]+$")

# Code member expressions that only look like host names: Path.home(),
# obj["local"], self.corp_id and friends are attributes, not topology.
CODE_MEMBER_RE = re.compile(r"^\s*[\(\[\.]")

# Complete private addresses only: all four octets have to be numeric, so the
# documentation forms 192.168.1.x and 10.0.0.x never match. Use RFC 5737 ranges
# (TEST-NET-1/2/3) in documentation when a complete address is needed.
PRIVATE_IP_RE = re.compile(
    r"\b(?:(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01])"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))"
    r"\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

MAC_RE = re.compile(
    r"(?i)\b(?!aa:bb:cc:dd:ee:)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")

HOSTNAME_RE = re.compile(
    r"(?i)\b[a-z0-9][a-z0-9\-]{2,}\.(?:internal|intranet|local|lan|corp|home|ad)\b")

PATTERNS = [
    ("cloud-access-key",
     re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private-key-block",
     re.compile(_F(r"-----BEGIN [A-Z ]*", r"PRIVATE KEY", r"-----"))),
    ("github-token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github-fine-grained-token",
     re.compile(_F(r"\bgithub", r"_pat_[A-Za-z0-9_]{20,}\b"))),
    ("openai-style-key",
     re.compile(_F(r"\bsk", r"-[A-Za-z0-9]{20,}\b"))),
    ("slack-token",
     re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key",
     re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("jwt",
     re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("credential-assignment",
     re.compile(r"(?i)\b(?:api[_-]?key|apikey|secret|passwd|password|passphrase|"
                r"access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key)\b"
                r"\s*[:=]\s*[\"']?(?!\s*$|\$|<|%|\{\{|your[-_]|example|xxx|redacted|"
                r"placeholder|none|null|true|false|os\.environ|getenv|process\.env|"
                r"env\[|secrets\.|changeme|change-me)[\"']?([^\s\"'#]{8,})")),
    ("bearer-literal",
     re.compile(r"(?i)\bAuthorization\b[^\n]{0,20}Bearer\s+(?!\$\{|\$|<|\{\{|%)([A-Za-z0-9._\-]{20,})")),
    ("account-id-literal",
     re.compile(r"(?i)\b(?:qq|uid|openid|uname|account|session[_-]?id|user[_-]?id)\b"
                r"[\"']?\s*[:=]\s*[\"']?(\d{6,}|[A-Za-z0-9]{20,})")),
    ("absolute-path", ABS_PATH_RE),
    ("private-ip", PRIVATE_IP_RE),
    ("mac-address", MAC_RE),
    ("internal-hostname", HOSTNAME_RE),
]

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
EMAIL_ALLOW_RE = re.compile(
    r"(?i)(?:noreply|no-reply|example\.(?:com|org|net)|invalid|localhost|"
    r"users\.noreply\.github\.com)$")

# Long random-looking strings. Tuned to avoid prose and ordinary identifiers:
# besides the length and the Shannon-entropy test, a candidate has to be
# vowel-poor. Random tokens are near-uniform over [A-Za-z0-9], so the a/e/i/o/u
# share stays around 0.05-0.17; identifiers, paths and prose sit at 0.28+ and
# are rejected. Measured on a calibration set of both kinds.
ENTROPY_RE = re.compile(r"\b[A-Za-z0-9+/=_\-]{32,}\b")
VOWEL_RATIO_MAX = 0.25
ENTROPY_MIN_BITS = 4.2
# A token carrying an RFC 4122 GUID is an identifier (device path, efivars name,
# registry key), not a secret - no credential is shaped like that.
GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# A hex digest (git sha, md5, sha256) is common in documentation and never a key.
HEXDIGEST_RE = re.compile(r"[a-f0-9]{32,64}")

INFO_RE = re.compile(r"<[A-Z_]{2,}>|TODO|FIXME|XXX|your-[a-z\-]+")

# Stock OpenWrt UCI section identifiers. They match the ".lan" host-name shape
# but are generic configuration names present in every OpenWrt installation,
# not local topology.
UCI_LAN_ALLOW = re.compile(
    r"^(?:dhcp|interface|firewall|wireless|network|system|wan|lan|loopback)\.lan$")


def mask(value, keep=4, cap=12):
    """Return a printable excerpt that never contains the whole value."""
    value = value.strip()
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * min(len(value) - keep, cap)


def shannon(text):
    from math import log2
    if not text:
        return 0.0
    counts = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    return -sum((c / n) * log2(c / n) for c in counts.values())


def entropy_hits(line):
    out = []
    for tok in ENTROPY_RE.findall(line):
        if HEXDIGEST_RE.fullmatch(tok):
            continue
        if GUID_RE.search(tok):        # identifier carrying a GUID, not a credential
            continue
        vowels = sum(ch in "aeiouAEIOU" for ch in tok) / len(tok)
        if vowels > VOWEL_RATIO_MAX:   # word-like / identifier-like, not a token
            continue
        if shannon(tok) >= ENTROPY_MIN_BITS:
            out.append(tok)
    return out


def rel_posix(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def skipped(rel, name, skip_names, skip_prefixes):
    if name in skip_names or rel in skip_names:
        return True
    for prefix in skip_prefixes:
        prefix = prefix.strip("/")
        if prefix and (rel == prefix or rel.startswith(prefix + "/")):
            return True
    return False


def iter_files(root, skip_names, skip_prefixes):
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = rel_posix(root, dirpath) if dirpath != root else "."
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and not skipped(rel_dir + "/" + d if rel_dir != "." else d,
                            d, set(), skip_prefixes)
        ]
        for name in filenames:
            rel = rel_dir + "/" + name if rel_dir != "." else name
            if skipped(rel, name, skip_names, skip_prefixes):
                continue
            if os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            yield os.path.join(dirpath, name)


def path_allowed(hit):
    """True when an absolute-path match is a placeholder, not a real path."""
    return bool(DOC_PATH_RE.search(hit)
                or CLASS_IN_PATH_RE.search(hit)
                or ELIDED_PATH_RE.match(hit.strip()))


def hostname_allowed(hit, line, end):
    """True when a host-name-shaped match is really a code member expression."""
    if UCI_LAN_ALLOW.match(hit):
        return True
    if hit[:1].isupper() and CODE_MEMBER_RE.match(line[end:end + 1] or "("):
        return True
    return False


def scan_file(path, max_bytes):
    findings, infos = [], []
    try:
        if os.path.getsize(path) > max_bytes:
            return findings, infos, "skipped (too large)"
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return findings, infos, "unreadable: %s" % exc
    if b"\x00" in raw[:4096]:
        return findings, infos, "skipped (binary)"
    text = raw.decode("utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, rx in PATTERNS:
            for match in rx.finditer(line):
                hit = match.group(0) or ""
                if name == "absolute-path" and path_allowed(hit):
                    continue
                if name == "internal-hostname" and hostname_allowed(hit, line, match.end()):
                    continue
                findings.append((lineno, name, mask(hit, keep=0) if name == "absolute-path" else mask(hit)))
        for match in EMAIL_RE.finditer(line):
            if not EMAIL_ALLOW_RE.search(match.group(0)):
                findings.append((lineno, "email-address", mask(match.group(0))))
        for tok in entropy_hits(line):
            findings.append((lineno, "high-entropy-token", mask(tok)))
        info = INFO_RE.search(line)
        if info:
            infos.append((lineno, "placeholder-or-todo", info.group(0)))
    return findings, infos, "ok"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scan a file or directory tree for secrets, credentials, "
                    "private IPs, MACs and identity leaks.")
    parser.add_argument("root", nargs="?", default=".",
                        help="File or directory to scan (default: .)")
    parser.add_argument("--exclude", action="append", default=[], metavar="NAME",
                        help="File name or repo-relative path to skip (repeatable).")
    parser.add_argument("--exclude-path", action="append", default=[],
                        dest="exclude_path", metavar="PREFIX",
                        help="Repo-relative path prefix to skip, e.g. "
                             "examples/fixtures (repeatable).")
    parser.add_argument("--max-bytes", type=int, default=2_000_000,
                        help="Skip files larger than this many bytes (default: 2000000).")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text.")
    parser.add_argument("--show-info", action="store_true",
                        help="Also print INFO lines (placeholders, TODOs).")
    parser.add_argument("--quiet", action="store_true",
                        help="Print the summary only.")
    args = parser.parse_args(argv)

    if os.path.isfile(args.root):
        root = os.path.dirname(os.path.abspath(args.root)) or "."
        targets = [os.path.abspath(args.root)]
        single = True
    elif os.path.isdir(args.root):
        root = args.root
        targets = None
        single = False
    else:
        print("not a file or directory: %s" % args.root, file=sys.stderr)
        return 2

    skip_names = set(args.exclude)
    skip_prefixes = list(args.exclude_path)
    if single:
        skip_names = set()
        skip_prefixes = []
        paths = targets
    else:
        paths = list(iter_files(root, skip_names, skip_prefixes))

    results, total = [], 0
    for path in paths:
        findings, infos, status = scan_file(path, args.max_bytes)
        rel = rel_posix(root, path) if not single else os.path.basename(path)
        if findings:
            total += len(findings)
        results.append({"file": rel, "status": status,
                        "findings": findings, "info": infos})

    if args.json:
        print(json.dumps({"root": root, "total_findings": total,
                          "files": results}, indent=1))
    else:
        for item in results:
            if item["status"] != "ok" and not args.quiet:
                print("  [%s] %s" % (item["status"], item["file"]))
            if not args.quiet:
                for lineno, name, sample in item["findings"]:
                    print("%s:%d: [%s] %s" % (item["file"], lineno, name, sample))
                if args.show_info:
                    for lineno, name, sample in item["info"]:
                        print("%s:%d: INFO [%s] %s" % (item["file"], lineno, name, sample))
        print("\nscanned %d file(s); findings: %d" % (len(results), total))
        print("RESULT: %s" % ("CLEAN" if total == 0 else "FINDINGS PRESENT"))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
