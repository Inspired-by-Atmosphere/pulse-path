#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pp_index.py — 指针 L2 检索索引（零依赖：stdlib sqlite3 + FTS5）
=====================================================================
pulse-path 的指针只给机器看：`key = scheme://目标 | 摘要`。摘要 ≤40 字，真正的内容
（L2）在 `doc://` 指向的文件、`skill://` 指向的技能目录里。过去要回答"这句话到底在
哪写过"，只能靠宿主 agent 自己读指针 + 逐个开文件；本脚本补上**可离线、零依赖**的
检索层。

三层分工：
    指针文件（MEMORY.md）  只决定"哪些 L2 目标值得入索引"（doc:// / skill://）
    L2 正文（.md）         入 SQLite FTS5 全文索引（trigram 分词，中文子串可搜）
    本脚本                 --build 建/更新、--search 查、--stats 体检索引本身

为什么要 trigram：`unicode61` 把整段中文切成一个词，查"悬空指针"只能命中完全相同的
整段；`trigram` 按三字滑窗切片，中文/英文子串都能命中。**代价**：FTS5 trigram 对
<3 字符的查询词无解，本脚本自动回退 LIKE 全表扫描，并在结果里 `mode=like` 明示
（不静默降级）。

索引文件：`PP_INDEX_PATH`，默认 `$HERMES_HOME/.pp_index/pp_index.sqlite`
    —— 目录自建、幂等、可随时删掉重建；**仓库与记忆文件一概不写**。
    `--rebuild` 全量重建；默认按 (mtime, size) 跳过未变文件（增量）。

用法:
    python pp_index.py --build                        # 增量建/更新
    python pp_index.py --build --rebuild              # 全量重建
    python pp_index.py --build --index-dir docs       # 额外索引目录内所有 .md（可重复）
    python pp_index.py --search 悬空指针 --limit 5     # 检索
    python pp_index.py --search 断链 --json           # 机器可读
    python pp_index.py --stats                        # 文档数/大小/建索引时间/是否陈旧
    python pp_index.py --selftest                     # 离线自测（临时夹具）

退出码:
    0 = 正常（**0 命中也是 0**，结果里明说 hits=0）
    2 = 环境/用法错误（索引不存在且没 --build、指针文件缺失、FTS5 不可用、schema 过旧）
"""
import argparse
import contextlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import time

try:                       # 跨平台：非 UTF-8 控制台/重定向下不因中文崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCHEMA_VERSION = 1
FTS_TOKENIZER = "trigram"          # 中文子串可搜的前提；<3 字符查询走 LIKE 回退
SNIPPET_PAD = 48                   # 片段窗口：命中词左右各取多少字符
MAX_FILE_BYTES = 4 * 1024 * 1024   # 单文件上限：超大文件不入索引（防内存/耗时爆炸）
INDEX_SUBDIR = ".pp_index"
INDEX_FILENAME = "pp_index.sqlite"
DEFAULT_LIMIT = 8
MAX_LIMIT = 200
MIN_FTS_CHARS = 3                  # trigram 的硬下限：少于 3 个字符无法参与 MATCH

# 指针行：与 mem_guard.py 完全同一口径（key = scheme://target | 摘要）
POINTER_RE = re.compile(r"^([a-z_][a-z0-9_]*) = ([a-z]+)://(\S+?) \| (.+)$")

# 遍历时永远跳过的目录（索引自身也跳过，防把自己索进去）
SKIP_DIRS = {".git", ".hg", ".svn", ".obsidian", ".trash", "node_modules", "__pycache__",
             ".venv", "venv", "env", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
             "dist", "build", ".idea", ".vscode", "site-packages", ".curator_backups",
             INDEX_SUBDIR}

DDL_TABLES = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    source TEXT,
    pointer_key TEXT,
    indexed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pointers (
    key TEXT PRIMARY KEY,
    scheme TEXT NOT NULL,
    target TEXT NOT NULL,
    summary TEXT,
    l2_paths TEXT
);
"""

DDL_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5("
           "path UNINDEXED, body, tokenize='%s')" % FTS_TOKENIZER)


class EnvError(Exception):
    """环境/用法错误 → 退出码 2。kind 会被 --json 原样输出，供 MCP 侧分类。"""

    def __init__(self, kind, message, hint=None):
        Exception.__init__(self, message)
        self.kind = kind
        self.message = message
        self.hint = hint


# ══════════════════════════════════════════════════════════════
# 路径配置（调用时求值：自测可在同一进程里换沙箱，不必重开解释器）
# ══════════════════════════════════════════════════════════════
def hermes_home():
    return os.path.abspath(os.path.expanduser(os.environ.get("HERMES_HOME") or os.path.join("~", ".hermes")))


def default_memory_file():
    """指针文件：--memory-file > $PP_INDEX_MEMORY > $MEM_GUARD_MEMORY > $HERMES_HOME/memories/MEMORY.md"""
    for name in ("PP_INDEX_MEMORY", "MEM_GUARD_MEMORY"):
        v = os.environ.get(name)
        if v:
            return os.path.abspath(os.path.expanduser(os.path.expandvars(v)))
    return os.path.join(hermes_home(), "memories", "MEMORY.md")


def index_path():
    v = os.environ.get("PP_INDEX_PATH")
    if v:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(v)))
    return os.path.join(hermes_home(), INDEX_SUBDIR, INDEX_FILENAME)


def skills_roots():
    """skill:// 的搜索根：$PP_INDEX_SKILLS（可用 os.pathsep 分隔多个）> $HERMES_HOME/skills"""
    raw = os.environ.get("PP_INDEX_SKILLS")
    if raw:
        out = [os.path.abspath(os.path.expanduser(os.path.expandvars(p)))
               for p in raw.split(os.pathsep) if p.strip()]
        if out:
            return out
    return [os.path.join(hermes_home(), "skills")]


def _expand(p):
    return os.path.expandvars(os.path.expanduser(p))


def _norm(p):
    """用于去重/比对的大小写规范化键（Windows 大小写不敏感）。"""
    return os.path.normcase(os.path.abspath(p))


# ══════════════════════════════════════════════════════════════
# ① 采集：指针 → L2 目标
# ══════════════════════════════════════════════════════════════
def parse_pointers(txt):
    """解析指针行（注释/空行/§ 分隔符跳过）。返回 [dict(key, scheme, target, summary, line)]。"""
    out = []
    for i, ln in enumerate((txt or "").split("\n"), 1):
        s = ln.strip()
        if not s or s.startswith("#") or s == "§":
            continue
        m = POINTER_RE.match(s)
        if m:
            out.append({"line": i, "key": m.group(1), "scheme": m.group(2),
                        "target": m.group(3), "summary": m.group(4)})
    return out


def resolve_doc_target(target, memory_file):
    """doc:// 目标 → 绝对路径（不存在返回 None）。

    解析顺序：绝对路径 → 当前工作目录 → $HERMES_HOME → 指针文件所在目录。
    与 mem_guard 的 path_exist 同源，只是多给了两个基准，避免"体检过、索引找不到"。
    """
    t = _expand(target or "").split("#", 1)[0].strip()
    if not t:
        return None
    t = t.replace("\\", os.sep).replace("/", os.sep)
    if os.path.isabs(t):
        return os.path.abspath(t) if os.path.exists(t) else None
    bases = [os.getcwd(), hermes_home(), os.path.dirname(os.path.abspath(memory_file))]
    for base in bases:
        cand = os.path.abspath(os.path.join(base, t))
        if os.path.exists(cand):
            return cand
    return None


def _skill_docs(skill_dir):
    """一个技能的 L2：SKILL.md + references/**/*.md（按链表律，引用文档也算正文）。"""
    sk = os.path.join(skill_dir, "SKILL.md")
    if os.path.isfile(sk):
        yield os.path.abspath(sk)
    ref = os.path.join(skill_dir, "references")
    if os.path.isdir(ref):
        for root, dirs, files in os.walk(ref):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                if f.lower().endswith(".md"):
                    yield os.path.abspath(os.path.join(root, f))


def resolve_skill_target(target):
    """skill://name → 该技能目录下的 md 列表（找不到返回 []）。"""
    t = (_expand(target or "")).strip().replace("\\", "/").strip("/")
    if not t:
        return []
    for root in skills_roots():
        if not os.path.isdir(root):
            continue
        direct = os.path.join(root, t.replace("/", os.sep))
        if os.path.isfile(direct) and direct.lower().endswith(".md"):
            return [os.path.abspath(direct)]
        if os.path.isdir(direct) and os.path.isfile(os.path.join(direct, "SKILL.md")):
            return list(_skill_docs(direct))
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            if os.path.basename(dirpath) == t and "SKILL.md" in files:
                return list(_skill_docs(dirpath))
    return []


def iter_md(dirpath):
    """目录内全部 .md（递归、跳过 SKIP_DIRS）。"""
    out = []
    for root, dirs, files in os.walk(dirpath):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if f.lower().endswith(".md"):
                out.append(os.path.abspath(os.path.join(root, f)))
    return out


def collect_targets(memory_file, index_dirs):
    """指针文件 + 额外目录 → (pointers, targets, unresolved, notes)。

    targets: {绝对路径: {"source": "doc|skill|dir", "pointer_key": key|None}}
    同一文件既是指针目标又在 --index-dir 里时，保留指针来源（反查才有 pointer_key）。
    """
    pointers, targets, unresolved, notes = [], {}, [], []
    txt = ""
    if os.path.isfile(memory_file):
        txt = io.open(memory_file, "r", encoding="utf-8", errors="ignore").read()
    else:
        notes.append("指针文件不存在: %s" % memory_file)

    for p in parse_pointers(txt):
        l2 = []
        if p["scheme"] == "doc":
            hit = resolve_doc_target(p["target"], memory_file)
            if hit is None:
                unresolved.append({"key": p["key"], "scheme": p["scheme"],
                                   "target": p["target"], "reason": "目标不存在"})
            elif os.path.isdir(hit):
                l2 = iter_md(hit)          # doc:// 指目录 = 整目录入索引
            else:
                l2 = [hit]
        elif p["scheme"] == "skill":
            l2 = resolve_skill_target(p["target"])
            if not l2:
                unresolved.append({"key": p["key"], "scheme": p["scheme"],
                                   "target": p["target"], "reason": "技能目录不存在"})
        # 其余 scheme（cfg/cron/script/...）没有 L2 正文，不入索引
        p["l2"] = [os.path.abspath(x) for x in l2]
        pointers.append(p)
        for path in p["l2"]:
            targets.setdefault(path, {"source": p["scheme"], "pointer_key": p["key"]})

    for d in index_dirs or []:
        d_abs = os.path.abspath(_expand(d))
        if not os.path.isdir(d_abs):
            unresolved.append({"key": None, "scheme": "dir",
                               "target": d_abs, "reason": "目录不存在"})
            continue
        for path in iter_md(d_abs):
            targets.setdefault(path, {"source": "dir", "pointer_key": None})

    return pointers, targets, unresolved, notes


# ══════════════════════════════════════════════════════════════
# ② 索引库
# ══════════════════════════════════════════════════════════════
def connect(index_file, create=True):
    path = os.path.abspath(_expand(index_file))
    if not create and not os.path.isfile(path):
        raise EnvError("index_missing",
                       "索引不存在: %s" % path,
                       hint="先运行 --build（或给 pp_search 传 rebuild=true）")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL_TABLES)
        try:
            conn.execute(DDL_FTS)
        except sqlite3.OperationalError as exc:
            conn.close()
            raise EnvError("fts5_unavailable",
                           "当前 sqlite3 未启用 FTS5（%s）" % exc,
                           hint="换用带 FTS5 的 CPython 3.9+（官方 Windows/macOS 安装包均自带）")
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise EnvError("index_unreadable", "索引无法打开（%s）: %s" % (exc, path),
                       hint="删掉索引文件后 --rebuild")
    return conn, path


def _meta_get(conn, key, default=None):
    row = conn.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return row[0] if row else default


def _check_schema(conn, path):
    got = _meta_get(conn, "schema")
    if got is not None and str(got) != str(SCHEMA_VERSION):
        raise EnvError("schema_mismatch",
                       "索引 schema=%s，本脚本需要 %d: %s" % (got, SCHEMA_VERSION, path),
                       hint="--rebuild 重建索引")


def _staleness(conn):
    """逐条 stat 已索引文件：mtime/size 变了 = changed；文件没了 = missing。"""
    changed, missing = [], []
    for path, mtime, size in conn.execute("SELECT path, mtime, size FROM docs").fetchall():
        try:
            st = os.stat(path)
        except OSError:
            missing.append(path)
            continue
        if abs(st.st_mtime - mtime) > 1e-6 or st.st_size != size:
            changed.append(path)
    return changed, missing


def index_snapshot(conn, path, with_stale=True):
    """索引概况（search 与 stats 共用）：docs/bytes/built_at/stale。"""
    docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    total = conn.execute("SELECT COALESCE(SUM(size), 0) FROM docs").fetchone()[0]
    snap = {
        "path": path,
        "schema": SCHEMA_VERSION,
        "docs": docs,
        "bytes": int(total),
        "built_at": _meta_get(conn, "built_at"),
        "memory_file": _meta_get(conn, "memory_file"),
        "index_dirs": json.loads(_meta_get(conn, "index_dirs") or "[]"),
        "pointers": int(_meta_get(conn, "pointers", "0") or 0),
        "size_bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
        "stale": False,
        "changed": [],
        "missing": [],
        "empty": docs == 0,
    }
    if with_stale:
        changed, missing = _staleness(conn)
        snap["changed"] = changed
        snap["missing"] = missing
        snap["stale"] = bool(changed or missing or docs == 0)
    return snap


def build_index(memory_file=None, index_dirs=None, rebuild=False, index_file=None):
    """建/更新索引。默认增量：mtime+size 未变的文件直接跳过。返回报告 dict。"""
    t0 = time.time()
    memory_file = os.path.abspath(_expand(memory_file or default_memory_file()))
    index_dirs = [os.path.abspath(_expand(d)) for d in (index_dirs or [])]
    pointers, targets, unresolved, notes = collect_targets(memory_file, index_dirs)

    conn, path = connect(index_file or index_path())
    try:
        if rebuild:
            conn.execute("DELETE FROM docs_fts")
            conn.execute("DELETE FROM docs")
        stats = {"targets": len(targets), "added": 0, "updated": 0, "skipped": 0,
                 "removed": 0, "failed": []}
        now = time.time()
        kept = set()
        for target in sorted(targets):
            info = targets[target]
            try:
                st = os.stat(target)
            except OSError as exc:
                stats["failed"].append({"path": target, "error": str(exc)})
                continue          # 不放进 kept → 旧行会被下面清掉（文件真没了）
            row = conn.execute("SELECT id, mtime, size FROM docs WHERE path = ?",
                               (target,)).fetchone()
            if row and abs(row[1] - st.st_mtime) < 1e-6 and row[2] == st.st_size:
                stats["skipped"] += 1
                kept.add(_norm(target))
                conn.execute("UPDATE docs SET source = ?, pointer_key = ? WHERE id = ?",
                             (info["source"], info["pointer_key"], row[0]))
                continue
            if st.st_size > MAX_FILE_BYTES:
                stats["failed"].append({"path": target,
                                        "error": "文件过大（>%d 字节），已跳过" % MAX_FILE_BYTES})
                continue
            try:
                body = io.open(target, "r", encoding="utf-8", errors="ignore").read()
            except OSError as exc:
                stats["failed"].append({"path": target, "error": str(exc)})
                continue
            if row:
                doc_id = row[0]
                conn.execute("UPDATE docs SET mtime=?, size=?, source=?, pointer_key=?, "
                             "indexed_at=? WHERE id=?",
                             (st.st_mtime, st.st_size, info["source"],
                              info["pointer_key"], now, doc_id))
                conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (doc_id,))
                stats["updated"] += 1
            else:
                cur = conn.execute("INSERT INTO docs (path, mtime, size, source, pointer_key, "
                                   "indexed_at) VALUES (?,?,?,?,?,?)",
                                   (target, st.st_mtime, st.st_size, info["source"],
                                    info["pointer_key"], now))
                doc_id = cur.lastrowid
                stats["added"] += 1
            conn.execute("INSERT INTO docs_fts (rowid, path, body) VALUES (?,?,?)",
                         (doc_id, target, body))
            kept.add(_norm(target))

        # 已不在目标集合里的文档（指针删了 / 文件改名）从索引移除
        for doc_id, old_path in conn.execute("SELECT id, path FROM docs").fetchall():
            if _norm(old_path) not in kept:
                conn.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
                conn.execute("DELETE FROM docs_fts WHERE rowid = ?", (doc_id,))
                stats["removed"] += 1

        # 指针表整表替换：指针文件是权威，不做增量猜测
        conn.execute("DELETE FROM pointers")
        for p in pointers:
            conn.execute("INSERT OR REPLACE INTO pointers (key, scheme, target, summary, l2_paths) "
                         "VALUES (?,?,?,?,?)",
                         (p["key"], p["scheme"], p["target"], p["summary"],
                          json.dumps(p["l2"], ensure_ascii=False)))
        built_at = time.strftime("%Y-%m-%d %H:%M:%S")
        meta = {"schema": SCHEMA_VERSION, "built_at": built_at, "memory_file": memory_file,
                "index_dirs": json.dumps(index_dirs, ensure_ascii=False),
                "pointers": len(pointers), "tokenizer": FTS_TOKENIZER}
        for k, v in meta.items():
            conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?,?)", (k, str(v)))
        conn.commit()
    finally:
        conn.close()

    conn, path = connect(path, create=False)
    try:
        snap = index_snapshot(conn, path)
    finally:
        conn.close()
    return {"ok": True, "index": snap, "build": stats, "unresolved": unresolved,
            "notes": notes, "elapsed_s": round(time.time() - t0, 3)}


# ══════════════════════════════════════════════════════════════
# ③ 检索
# ══════════════════════════════════════════════════════════════
def make_snippet(body, terms, pad=SNIPPET_PAD):
    """截取命中词附近的文字并加 **…** 标记；无字面命中则取开头。"""
    text = body or ""
    low = text.lower()
    first = None
    for t in terms:
        idx = low.find(t.lower())
        if idx >= 0 and (first is None or idx < first):
            first = idx
    if first is None:
        window, prefix, suffix = text[: pad * 2], "", "…"
    else:
        start, end = max(0, first - pad), min(len(text), first + pad)
        window = text[start:end]
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(text) else ""
    window = re.sub(r"\s+", " ", window).strip()
    low_win = window.lower()
    marks = [(low_win.find(t.lower()), len(t)) for t in terms]
    marks = [m for m in marks if m[0] >= 0]
    if marks:
        pos, ln = min(marks, key=lambda x: x[0])
        before = window[pos - 1] if pos > 0 else ""
        after = window[pos + ln] if pos + ln < len(window) else ""
        if not (before == "*" and after == "*"):     # 正文本身已加粗时别叠成 ****词****
            window = window[:pos] + "**" + window[pos:pos + ln] + "**" + window[pos + ln:]
    return (prefix + window + suffix).strip()


def _pointer_reverse_map(conn):
    """doc 路径 → 引用它的指针 key 列表（来自指针表，供 search 反查）。"""
    out = {}
    for key, l2 in conn.execute("SELECT key, l2_paths FROM pointers").fetchall():
        try:
            paths = json.loads(l2 or "[]")
        except ValueError:
            continue
        for p in paths:
            out.setdefault(_norm(p), []).append(key)
    return out


def _fts_hits(conn, terms):
    """FTS5 路径：每个词作短语，词间 AND。"""
    match = " AND ".join('"%s"' % t.replace('"', '""') for t in terms)
    # bm25() 不收表别名，只能写实体表名 docs_fts（SQLite FTS5 的硬要求）
    return conn.execute(
        "SELECT d.path, d.pointer_key, d.source, docs_fts.body, "
        "-bm25(docs_fts, 0.0, 1.0) AS score "
        "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid "
        "WHERE docs_fts MATCH ? ORDER BY score DESC", (match,)).fetchall()


def _like_hits(conn, terms):
    """LIKE 回退：每个词都必须出现（等价 FTS 的 AND 语义），score=出现次数之和。"""
    rows = conn.execute(
        "SELECT d.path, d.pointer_key, d.source, f.body "
        "FROM docs d JOIN docs_fts f ON f.rowid = d.id").fetchall()
    low_terms = [t.lower() for t in terms]
    out = []
    for path, pkey, source, body in rows:
        low = (body or "").lower()
        if not all(t in low for t in low_terms):
            continue
        out.append((path, pkey, source, body, float(sum(low.count(t) for t in low_terms))))
    out.sort(key=lambda r: (-r[4], r[0]))
    return out


def search_index(query, limit=DEFAULT_LIMIT, index_file=None):
    """检索 L2 正文。返回 {query, mode, hits, hits_total, index, note}。"""
    q = (query or "").strip()
    if not q:
        raise EnvError("bad_query", "查询词为空", hint="--search 后面给一个词，例如 --search 悬空指针")
    if not isinstance(limit, int) or limit < 1 or limit > MAX_LIMIT:
        raise EnvError("bad_limit", "limit 需为 1-%d 的整数" % MAX_LIMIT)

    conn, path = connect(index_file or index_path(), create=False)
    try:
        _check_schema(conn, path)
        terms = q.split()
        mode, note = "fts", None
        if any(len(t) < MIN_FTS_CHARS for t in terms):
            mode = "like"
            note = ("查询词含少于 %d 个字符的片段，FTS5 trigram 无法匹配，"
                    "已回退 LIKE 全表扫描（结果按出现次数排序）" % MIN_FTS_CHARS)
            rows = _like_hits(conn, terms)
        else:
            try:
                rows = _fts_hits(conn, terms)
            except sqlite3.OperationalError as exc:      # 查询语法被 FTS5 拒绝
                mode = "like"
                note = "FTS5 拒绝该查询（%s），已回退 LIKE 全表扫描" % exc
                rows = _like_hits(conn, terms)

        p_map = _pointer_reverse_map(conn)
        hits = []
        for row in rows:
            path_, pkey, source, body, score = row
            hit = {"path": path_, "score": round(float(score), 4),
                   "snippet": make_snippet(body, terms), "source": source}
            keys = p_map.get(_norm(path_)) or ([pkey] if pkey else [])
            if keys:
                hit["pointer_key"] = keys[0]
                if len(keys) > 1:
                    hit["pointer_keys"] = keys
            hits.append(hit)
        total = len(hits)
        hits = hits[:limit]
        snap = index_snapshot(conn, path)
    finally:
        conn.close()

    payload = {"ok": True, "query": q, "limit": limit, "mode": mode,
               "hits_total": total, "hits": hits, "index": snap}
    if note:
        payload["note"] = note
    if total == 0:
        payload["note"] = ((payload.get("note") + "；") if payload.get("note") else "") + \
            "命中 0 条（hits=0）：索引 docs=%d，可换关键词、加 --index-dir 或 --rebuild" % snap["docs"]
    return payload


def index_stats(index_file=None):
    conn, path = connect(index_file or index_path(), create=False)
    try:
        _check_schema(conn, path)
        snap = index_snapshot(conn, path)
    finally:
        conn.close()
    return {"ok": True, "index": snap}


# ══════════════════════════════════════════════════════════════
# ④ 输出
# ══════════════════════════════════════════════════════════════
def render_build(rep):
    idx, b = rep["index"], rep["build"]
    lines = ["pp_index 建索引%s — 目标 %d，新增 %d，更新 %d，跳过 %d，移除 %d（%.2fs）"
             % ("（全量）" if b["skipped"] == 0 and b["added"] == idx["docs"] else "",
                b["targets"], b["added"], b["updated"], b["skipped"], b["removed"],
                rep["elapsed_s"]),
             "索引: %s（docs=%d, 正文 %d 字节, 文件 %d 字节, tokenizer=%s）"
             % (idx["path"], idx["docs"], idx["bytes"], idx["size_bytes"], FTS_TOKENIZER),
             "指针: %d 条（未解析 L2 目标 %d 个）" % (idx["pointers"], len(rep["unresolved"]))]
    for u in rep["unresolved"][:10]:
        lines.append("  ⚠️ %s → %s（%s）" % (u["key"] or "dir", u["target"], u["reason"]))
    for n in rep["notes"]:
        lines.append("  ℹ️ " + n)
    for f in rep["build"]["failed"][:5]:
        lines.append("  ⚠️ 跳过 %s（%s）" % (f["path"], f["error"]))
    return lines


def render_search(rep):
    idx = rep["index"]
    lines = ["查询「%s」mode=%s 命中 %d 条（显示 %d）| 索引 docs=%d built_at=%s stale=%s"
             % (rep["query"], rep["mode"], rep["hits_total"], len(rep["hits"]),
                idx["docs"], idx["built_at"], "true" if idx["stale"] else "false")]
    if not rep["hits"]:
        lines.append("  命中 0 条（hits=0）")
    for i, h in enumerate(rep["hits"], 1):
        tail = "  [pointer_key=%s]" % h["pointer_key"] if h.get("pointer_key") else ""
        lines.append("  %d) score %.4f%s" % (i, h["score"], tail))
        lines.append("     %s" % h["snippet"])
        lines.append("     %s" % h["path"])
    if rep.get("note"):
        lines.append("  ℹ️ " + rep["note"])
    return lines


def render_stats(rep):
    idx = rep["index"]
    lines = ["pp_index 索引体检",
             "  索引: %s（schema=%s, 文件 %d 字节）"
             % (idx["path"], idx["schema"], idx["size_bytes"]),
             "  文档: %d 篇 / 正文 %d 字节" % (idx["docs"], idx["bytes"]),
             "  指针: %s 条" % idx["pointers"],
             "  建索引时间: %s" % (idx["built_at"] or "（未知）"),
             "  指针文件: %s" % (idx["memory_file"] or "（未记录）"),
             "  是否陈旧: %s" % ("是（需 --build）" if idx["stale"] else "否")]
    for p in idx["changed"][:8]:
        lines.append("    · 已变更未重建: %s" % p)
    for p in idx["missing"][:8]:
        lines.append("    · 已消失仍留在索引: %s" % p)
    if idx["empty"]:
        lines.append("    · 索引为空：先 --build")
    return lines


def emit(payload, as_json, text_lines):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("\n".join(text_lines))


def fail(err, as_json, code=2):
    payload = {"ok": False, "error": {"kind": err.kind, "message": err.message}}
    if err.hint:
        payload["error"]["hint"] = err.hint
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        sys.stderr.write("❌ %s\n" % err.message)
        if err.hint:
            sys.stderr.write("   %s\n" % err.hint)
    return code


# ══════════════════════════════════════════════════════════════
# ⑤ 自测（临时夹具；不读也不写本机数据）
# ══════════════════════════════════════════════════════════════
def selftest():
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))
        print("  %s %s" % ("✅" if cond else "❌", name))

    def run_cli(argv):
        """同进程跑 CLI，吃掉它自己的打印（自测报告只留 ✅/❌ 行）。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue()

    sandbox = tempfile.mkdtemp(prefix="pp_index_")
    env_keys = ("HERMES_HOME", "PP_INDEX_PATH", "PP_INDEX_MEMORY", "MEM_GUARD_MEMORY",
                "PP_INDEX_SKILLS")
    saved = {k: os.environ.get(k) for k in env_keys}
    try:
        home = os.path.join(sandbox, "home")
        idx_dir = os.path.join(sandbox, "idx")
        extra = os.path.join(sandbox, "extra")

        def put(path, text):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return path

        mem = put(os.path.join(home, "memories", "MEMORY.md"),
                  "§\n"
                  "alpha = doc://pp-idx-fixture-alpha.md | 悬空指针样例文档\n"
                  "beta = doc://pp-idx-fixture-missing.md | 故意悬空的目标\n"
                  "demo = skill://demo-skill | 演示技能（SKILL.md+references）\n")
        alpha = put(os.path.join(home, "pp-idx-fixture-alpha.md"),
                    "# 样例\n\n这段讲悬空指针：指针目标不存在时叫悬空指针。\n"
                    "dangling pointer means the L2 target vanished.\n")
        skill_md = put(os.path.join(home, "skills", "demo-skill", "SKILL.md"),
                       "# demo-skill\n\n技能正文：治理引擎与记忆水位。\n")
        ref_md = put(os.path.join(home, "skills", "demo-skill", "references", "notes.md"),
                     "# 参考\n\n参考条目：指针体检要抓悬空指针。\n")
        cn_md = put(os.path.join(extra, "chinese.md"),
                    "# 目录内文档\n\n目录里的中文也能被检索：悬空指针。\n")
        en_md = put(os.path.join(extra, "english.md"),
                    "# english\n\ndangling pointer detection for agents.\n")

        os.environ["HERMES_HOME"] = home
        os.environ["PP_INDEX_PATH"] = os.path.join(idx_dir, "pp_index.sqlite")
        for k in ("PP_INDEX_MEMORY", "MEM_GUARD_MEMORY", "PP_INDEX_SKILLS"):
            os.environ.pop(k, None)

        # ① 建索引
        rep = build_index(memory_file=mem, index_dirs=[extra])
        docs = rep["index"]["docs"]
        conn, _p = connect(os.environ["PP_INDEX_PATH"], create=False)
        try:
            indexed = {os.path.normcase(p) for (p,) in conn.execute("SELECT path FROM docs")}
        finally:
            conn.close()
        want = [alpha, skill_md, ref_md, cn_md, en_md]
        check("--build 索引全部 L2（doc 文件 + SKILL.md + references + 目录）",
              all(os.path.normcase(p) in indexed for p in want) and docs == len(want))
        check("悬空 doc 目标不入索引",
              all("missing" not in os.path.normcase(p) for p in indexed)
              and any(u["key"] == "beta" for u in rep["unresolved"]))
        check("build 报告 index.docs/bytes/size_bytes 齐备",
              isinstance(rep["index"]["bytes"], int) and rep["index"]["size_bytes"] > 0)
        check("build 报告记录 built_at", bool(rep["index"]["built_at"]))

        # ② 中文检索（trigram）+ 指针反查
        cn = search_index("悬空指针")
        cn_paths = [h["path"] for h in cn["hits"]]
        check("中文 4 字查询命中（mode=%s）" % cn["mode"], cn["mode"] == "fts" and cn["hits_total"] >= 3)
        check("中文命中含 doc:// 指向的文件", os.path.normcase(alpha) in [os.path.normcase(p) for p in cn_paths])
        alpha_hit = next((h for h in cn["hits"]
                          if os.path.normcase(h["path"]) == os.path.normcase(alpha)), {})
        check("命中反查到 pointer_key=alpha", alpha_hit.get("pointer_key") == "alpha")
        ref_hit = next((h for h in cn["hits"]
                        if os.path.normcase(h["path"]) == os.path.normcase(ref_md)), {})
        check("references 文件反查到 pointer_key=demo", ref_hit.get("pointer_key") == "demo")
        check("目录内文档无指针来源（不编造 pointer_key）",
              "pointer_key" not in next((h for h in cn["hits"]
                                         if os.path.normcase(h["path"]) == os.path.normcase(cn_md)), {}))
        check("snippet 带 ** 片段标记**", "**" in alpha_hit.get("snippet", ""))
        check("snippet 截取命中词附近（带省略号边界）",
              "悬空指针" in alpha_hit.get("snippet", "") and "…" in alpha_hit.get("snippet", ""))

        # ③ 英文检索
        en = search_index("dangling")
        en_paths = [os.path.normcase(h["path"]) for h in en["hits"]]
        check("英文查询命中", en["mode"] == "fts" and os.path.normcase(en_md) in en_paths
              and os.path.normcase(alpha) in en_paths)

        # ④ 0 命中是正常结果
        none = search_index("zzz不存在的词zzz")
        check("0 命中 hits_total=0 且 hits=[]", none["hits_total"] == 0 and none["hits"] == [])
        check("0 命中带 note 明示 hits=0", "hits=0" in (none.get("note") or ""))

        # ⑤ 短词（<3 字符）→ LIKE 回退
        short = search_index("指针")
        check("2 字查询回退 LIKE 且有命中（mode=%s）" % short["mode"],
              short["mode"] == "like" and short["hits_total"] >= 2)
        check("LIKE 回退带提示 note", bool(short.get("note")))
        check("LIKE 回退仍带 snippet 标记", all("**" in h["snippet"] for h in short["hits"]))

        # ⑥ --limit 生效
        lim = search_index("悬空指针", limit=1)
        check("--limit 截断 hits 但 hits_total 保留全量",
              len(lim["hits"]) == 1 and lim["hits_total"] >= 3)

        # ⑦ 增量：未变文件跳过
        rep2 = build_index(memory_file=mem, index_dirs=[extra])
        check("增量第二次 --build 全部跳过", rep2["build"]["skipped"] == rep2["index"]["docs"]
              and rep2["build"]["added"] == 0 and rep2["build"]["updated"] == 0)
        check("增量后 docs 数不变（幂等）", rep2["index"]["docs"] == docs)

        # ⑧ 改一个文件 → 只重索引它
        time.sleep(0.01)
        put(cn_md, "# 目录内文档\n\n改过的内容：悬空指针与非悬空引用。\n")
        rep3 = build_index(memory_file=mem, index_dirs=[extra])
        check("改动后只更新 1 篇", rep3["build"]["updated"] == 1 and rep3["build"]["added"] == 0)
        check("改动内容已可检索", search_index("非悬空引用")["hits_total"] == 1)

        # ⑨ --rebuild 全量
        rep4 = build_index(memory_file=mem, index_dirs=[extra], rebuild=True)
        check("--rebuild 全量重建（skipped=0）", rep4["build"]["skipped"] == 0
              and rep4["build"]["added"] == rep4["index"]["docs"])

        # ⑩ --stats
        st = index_stats()["index"]
        check("--stats 字段齐备（docs/bytes/built_at/path/stale）",
              all(k in st for k in ("docs", "bytes", "built_at", "path", "stale"))
              and st["docs"] == docs and st["path"] == os.path.abspath(os.environ["PP_INDEX_PATH"]))
        check("刚建完的索引不算陈旧", st["stale"] is False)
        time.sleep(0.01)
        put(alpha, "# 样例\n\n又改了：悬空指针。\n")
        st2 = index_stats()["index"]
        check("文件比索引新 → stale=True", st2["stale"] is True
              and any(os.path.normcase(p) == os.path.normcase(alpha) for p in st2["changed"]))

        # ⑪ 索引文件消失 → missing
        os.remove(skill_md)
        st3 = index_stats()["index"]
        check("已删除文件进 missing 列表", any(os.path.normcase(p) == os.path.normcase(skill_md)
                                       for p in st3["missing"]))
        put(skill_md, "# demo-skill\n\n技能正文：治理引擎与记忆水位。\n")

        # ⑫ --json 可解析（命令行路径）
        rc_json, out_json = run_cli(["--search", "悬空指针", "--json"])
        try:
            parsed = json.loads(out_json)
        except ValueError:
            parsed = None
        check("--search --json 输出可解析 JSON", rc_json == 0 and isinstance(parsed, dict)
              and parsed.get("query") == "悬空指针" and isinstance(parsed.get("hits"), list))
        check("--json 的 index 子段含 docs/built_at/stale",
              all(k in (parsed or {}).get("index", {}) for k in ("docs", "built_at", "stale")))

        # ⑬ 缺索引 → 退出码 2（文本 + JSON 两种）
        os.environ["PP_INDEX_PATH"] = os.path.join(sandbox, "nope", "x.sqlite")
        rc_missing, out_missing = run_cli(["--search", "悬空指针"])
        rc_missing_json, out_missing_json = run_cli(["--search", "悬空指针", "--json"])
        try:
            err_payload = json.loads(out_missing_json)
        except ValueError:
            err_payload = {}
        check("缺索引时 --search 退出码 2 且 stdout 无噪声", rc_missing == 2 and out_missing == "")
        check("缺索引 --json 也是 2 且 error.kind=index_missing",
              rc_missing_json == 2 and (err_payload.get("error") or {}).get("kind") == "index_missing")
        check("缺索引时 --stats 退出码 2", run_cli(["--stats"])[0] == 2)
        check("缺索引时 --build 会补建（rc=0）",
              run_cli(["--build", "--memory-file", mem, "--index-dir", extra])[0] == 0)

        # ⑭ 空查询词
        check("空查询词退出码 2", run_cli(["--search", "   "])[0] == 2)

        # ⑮ 索引可删可重建
        os.remove(os.environ["PP_INDEX_PATH"])
        rep5 = build_index(memory_file=mem, index_dirs=[extra])
        check("删掉索引文件后可重建（可删性）", rep5["index"]["docs"] == docs)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)

    ok = sum(1 for _n, c in checks if c)
    print("\nSELFTEST %s %d/%d" % ("OK" if ok == len(checks) else "FAIL", ok, len(checks)))
    return 0 if ok == len(checks) else 1


# ══════════════════════════════════════════════════════════════
# ⑥ CLI
# ══════════════════════════════════════════════════════════════
EPILOG = """示例:
  python pp_index.py --build --index-dir docs
  python pp_index.py --search 悬空指针 --limit 5
  python pp_index.py --stats --json

环境变量:
  HERMES_HOME       记忆库根（默认 ~/.hermes）
  PP_INDEX_PATH     索引文件（默认 $HERMES_HOME/.pp_index/pp_index.sqlite）
  PP_INDEX_MEMORY   指针文件（等价 --memory-file）
  MEM_GUARD_MEMORY  指针文件（mem_guard 的口径，作为次级回退）
  PP_INDEX_SKILLS   skill:// 搜索根（os.pathsep 分隔，默认 $HERMES_HOME/skills）

退出码: 0 正常（0 命中也是 0）；2 环境/用法错误"""


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pp_index.py",
        description="pulse-path 指针 L2 检索索引（零依赖：sqlite3 + FTS5 trigram）",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="建/更新索引（默认增量）")
    ap.add_argument("--rebuild", action="store_true", help="全量重建（忽略 mtime/size 跳过）")
    ap.add_argument("--search", metavar="QUERY", help="检索 L2 正文")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="命中上限（默认 %d，1-%d）" % (DEFAULT_LIMIT, MAX_LIMIT))
    ap.add_argument("--stats", action="store_true", help="索引体检：文档数/大小/时间/是否陈旧")
    ap.add_argument("--index-dir", action="append", default=[], metavar="DIR",
                    help="额外索引目录内所有 .md（可重复）")
    ap.add_argument("--memory-file", metavar="PATH", help="指针文件（默认 $HERMES_HOME/memories/MEMORY.md）")
    ap.add_argument("--json", action="store_true", help="机器可读输出（stdout 只出 JSON）")
    ap.add_argument("--selftest", action="store_true", help="离线自测（临时夹具）")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    try:
        if args.build:
            rep = build_index(memory_file=args.memory_file, index_dirs=args.index_dir,
                              rebuild=args.rebuild)
            emit(rep, args.json, render_build(rep))
            return 0
        if args.search is not None:
            rep = search_index(args.search, limit=args.limit)
            emit(rep, args.json, render_search(rep))
            return 0
        if args.stats:
            rep = index_stats()
            emit(rep, args.json, render_stats(rep))
            return 0
    except EnvError as exc:
        return fail(exc, args.json)

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
