# pulse-path MCP server

Expose the repository's read-only health / lint engines — plus an offline full-text
search over pointer L2 bodies — to any MCP client (Claude Desktop, Claude Code, or
your own agent) over stdio.

- **Zero dependencies** — standard-library Python only (no `mcp`, no `fastmcp`, no
  `pydantic`). Nothing to `pip install`. Search uses the stdlib `sqlite3` + FTS5.
- **Read-only and idempotent** — every tool inspects, none of them writes to the
  repo or to the memory store. `mem_guard` always runs with `--check`. The one file
  any tool touches is `pp_index.py`'s own disposable index cache (see below).
- **Self-contained** — the server locates the repo from `__file__`, so it starts
  from any working directory.

Files:

| Path | Purpose |
| --- | --- |
| `mcp_server/pulse_path_mcp.py` | the server (stdio JSON-RPC 2.0) |
| `mcp_server/test_mcp.py` | self-test: spawns the server, no MCP client needed |
| `mcp_server/README.md` | this file |

## Tools

| Tool | Engine it calls | Arguments | Returns |
| --- | --- | --- | --- |
| `pp_guard_check` | `skills/memory-pointer-system/scripts/mem_guard.py --check --verbose` | `memory_file?`, `timeout_s?` | `counts {red, yellow, normal, pending, pointers_total}`, `red[]`, `yellow[]`, `clean`, `raw_report` |
| `pp_scan_links` | `skills/pulse-path/scripts/scan_links.py` | `dirs[]?`, `timeout_s?` | `errors_total`, `clean`, per-directory `dangling_paths[]` / `dead_skill_refs[]` / `fact_refs`, `findings_truncated` |
| `pp_l2_coverage` | `skills/memory-pointer-system/scripts/check_l2_coverage.py` | `memory_file?`, `timeout_s?` | `entries[]` (`key`, `scheme`, `summary_chars`, `l2_chars`, `missing_at_L2[]`), `missing_at_L2[]`, `clean` |
| `pp_secrets_scan` | `scripts/check_secrets.py --json` | `path?`, `timeout_s?` | `total_findings`, `clean`, `result` (`CLEAN` / `FINDINGS PRESENT`), masked `findings[]` |
| `pp_search` | `skills/memory-pointer-system/scripts/pp_index.py --search --json` | `query`, `limit?`, `rebuild?`, `timeout_s?` | `hits[]` (`path`, `score`, `snippet`, `pointer_key?`), `hits_total`, `mode` (`fts` / `like`), `index` (`docs`, `built_at`, `stale`), `raw_report` |

Path arguments may be absolute, or relative to the repo root; `~`, `$HERMES_HOME`
and `%VAR%` are expanded. Omitted paths fall back to the engine defaults
(`$MEM_GUARD_MEMORY`, else `$HERMES_HOME/memories/MEMORY.md`; repo root for scans).

Findings are **results, not errors**: `isError` is only `true` when the engine
could not run (missing script, timeout, non-zero-with-garbage-output, missing
input file). `clean: false` is a normal, successful call.

`pp_search` searches the L2 bodies behind the pointer dictionary — the files
`doc://` points at plus `SKILL.md` and `references/*.md` of every `skill://`
target — through an SQLite FTS5 index with the `trigram` tokenizer, so Chinese and
English substrings both match. Notes:

- **`rebuild: true` writes only its own cache.** `pp_index.py` stores the index at
  `$HERMES_HOME/.pp_index/pp_index.sqlite` (override with `PP_INDEX_PATH`), never in
  the repo and never in the memory store. The file is disposable: delete it and the
  next `rebuild: true` recreates it. Without `rebuild` the tool never writes anything
  and answers `status: "index_missing"` when no index exists yet.
- **Updates are incremental.** A rebuild skips files whose mtime and size are
  unchanged, so calling with `rebuild: true` on every query stays cheap.
- **Queries under 3 characters** cannot be matched by a trigram index, so the engine
  falls back to a `LIKE` scan and says `mode: "like"`. Zero hits is a successful call
  with `hits: []` — not an error.
- **`index.stale: true`** means a file changed after the index was built; call again
  with `rebuild: true` to refresh it. Hits always carry `pointer_key` when the file is
  reachable from a pointer (reverse lookup through the pointer table).

## Run it

```bash
python mcp_server/pulse_path_mcp.py                 # speak JSON-RPC on stdio
python mcp_server/pulse_path_mcp.py --help          # human help, serves nothing
python mcp_server/pulse_path_mcp.py --print-tools   # dump tool schemas
python mcp_server/pulse_path_mcp.py --version
python mcp_server/test_mcp.py                       # -> MCP TEST OK 76/76
```

Use `python3` instead of `python` where that is the interpreter name. On Windows
the configs below must point at the script's absolute path (the shell is not
involved, so `~` in `args` is not expanded by a shell — the server expands it).

## Register it

### Claude Desktop

Edit `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\claude_desktop_config.json`,
macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`), then
restart Claude Desktop:

```json
{
  "mcpServers": {
    "pulse-path": {
      "command": "python",
      "args": ["C:/path/to/pulse-path/mcp_server/pulse_path_mcp.py"],
      "env": {
        "HERMES_HOME": "C:/path/to/hermes-home"
      }
    }
  }
}
```

### Claude Code

Project scope, one command:

```bash
claude mcp add --scope project pulse-path -- python /path/to/pulse-path/mcp_server/pulse_path_mcp.py
```

or commit a `.mcp.json` next to the repo root (same shape as above):

```json
{
  "mcpServers": {
    "pulse-path": {
      "type": "stdio",
      "command": "python",
      "args": ["C:/path/to/pulse-path/mcp_server/pulse_path_mcp.py"],
      "env": {}
    }
  }
}
```

Keep the JSON comment-free: `mcpServers` configs are strict JSON, and a `//` line
will make the client drop the whole server silently.

### Any other client

`command = python`, `args = ["<repo>/mcp_server/pulse_path_mcp.py"]`, transport
`stdio`. Only stdout carries protocol frames; the single startup line
(`[pulse-path-mcp] serving ...`) goes to stderr, so log capture will not corrupt
the stream.

## Transport and protocol

- JSON-RPC 2.0 over stdin/stdout.
- **Framing: newline-delimited JSON** — one UTF-8 message per line, and a message
  never contains a raw newline (`MCP` stdio transport framing). The reader also
  accepts LSP-style `Content-Length: N` header frames as a fallback, detected per
  message, so a client using the older header form still works; the server itself
  only ever *emits* newline-delimited frames.
- `initialize` answers with `protocolVersion` (the client's own version when it is
  one of `2025-06-18` / `2025-03-26` / `2024-11-05` / `2024-10-07`, otherwise the
  newest), `capabilities.tools`, `serverInfo`, and `instructions`.
- `notifications/initialized` is accepted and never answered (notifications get no
  response at all).
- `tools/list`, `tools/call`, `ping` are implemented; unknown methods return
  `-32601`, unknown tools and bad arguments return `-32602`, malformed JSON returns
  `-32700`.
- `tools/call` results carry both `content[0].text` (the same JSON, for clients
  without structured-output support) and `structuredContent`.

## Configuration

| Variable | Effect |
| --- | --- |
| `PULSE_PATH_MCP_TIMEOUT` | per-engine timeout in seconds (default `120`, clamped to 1–600) |
| `HERMES_HOME` | memory store root the engines use by default (`memories/`, `skills/`, `reports/`, `.pp_index/`) |
| `MEM_GUARD_*`, `SKILLS_ROOT`, `VIKING_ROOT` | passed straight through to the engines |
| `PP_INDEX_PATH` | where `pp_search` keeps its FTS5 index (default `$HERMES_HOME/.pp_index/pp_index.sqlite`) |
| `PP_INDEX_MEMORY` / `PP_INDEX_SKILLS` | pointer file and `skill://` search roots used when the index is built |

Every tool also accepts `timeout_s` for a single call. Set these in the client's
`env` block; the server inherits its own environment and passes it to the engines
unchanged.

## Limits and known behaviour

1. **Human-readable reports are parsed.** `mem_guard`, `scan_links` and
   `check_l2_coverage` print prose for people; only `check_secrets.py` emits JSON
   (`--json`). This server parses that prose and always returns the untouched text
   as `raw_report`. The engines are owned elsewhere and are deliberately not
   modified here, so a format change degrades rather than breaks: unknown lines are
   ignored, and `pp_guard_check` adds a `warning` field when its parsed red count
   disagrees with the report header. If an engine's output becomes unparseable the
   call still succeeds with `counts` at zero and `raw_report` intact — read
   `raw_report` when a number looks impossible.
2. **`pp_scan_links` lists at most 15 findings per category per directory** — that
   is the engine's own preview cap. `errors_total` and each `counts` entry are the
   engine's exact totals, and `findings_truncated` / `hidden_findings` tell you the
   lists are partial. Scan a narrower directory to see everything.
3. **`pp_l2_coverage` only inspects pointers whose summary is longer than 40
   characters** (engine rule). A token missing at L2 may be *stale* rather than
   missing — verify before deleting it from a summary. `viking://` targets are
   semantic search terms and always report as unresolvable.
4. **`mem_guard` keeps its own audit log.** `--check` guarantees the pointer file is
   never rewritten, but the engine itself appends its report to
   `$HERMES_HOME/reports/mem_guard-report.md`. That is engine behaviour, not
   something this server adds; point `HERMES_HOME` at a scratch directory if you do
   not want a shared agent's log touched. Nothing else is written anywhere.
5. **Exit code 1 is not a failure** for `scan_links` (findings) or
   `check_secrets.py` (leaks). Both are reported as successful calls with
   `clean: false`.
6. **Not implemented:** HTTP/SSE transport, authentication, resources, prompts,
   sampling, server-initiated notifications, and `tools/list_changed`. There are no
   write tools by design.
7. **Timeouts kill the engine, not the server.** A timed-out call returns
   `status: "engine_error"`, `error.kind: "timeout"` and the budget used; the
   server keeps serving.
8. **No network access** is made by the server or by any engine it calls.

## Self-test

`test_mcp.py` builds a throwaway `HERMES_HOME` in a temp directory (one resolvable
pointer, one deliberately dangling pointer, a clean and a dirty directory, and a
small skill whose `SKILL.md` plus one file under its `references/` directory carry
Chinese text), spawns
the server as a real child process, and drives the full MCP handshake plus every
tool over raw pipes:

```bash
python mcp_server/test_mcp.py
MCP TEST OK 76/76
```

It asserts the handshake fields, notification silence, tool schemas, parsed
guard/scan/L2/secrets payloads, the `pp_search` path (index_missing before a build,
`rebuild: true` producing hits with `pointer_key`, `hits: []` for zero matches),
protocol error codes, the `Content-Length` reader fallback, the `script_missing`
branch and the report parser — offline, no MCP client library, no real memory store.
