# 对接外部记忆服务（`store://`）——只对接，不 vendor

本仓维护的是**指针字典**：`key = scheme://target | 摘要`，摘要 ≤40 字，指针只给机器看。
语义存储、向量检索、图检索这些能力不属于指针字典，交给**外部**记忆服务。
本文说明为什么这么划线、`store://` 怎么配、三类服务怎么接进来，以及它刻意不做的事。

---

## 1. 为什么不 vendor

`awesome-agent-memory` 的 MCP 区里挑出来的候选，直接拷进本仓都会把"零依赖 MIT"这件事弄坏：

| 候选 | 许可 | 拷进来要付出的代价 |
|---|---|---|
| Basic Memory | AGPL-3.0 | 30 多个第三方依赖；AGPL-3.0 是强 copyleft |
| mem0 | Apache-2.0 | 要向量库，写入时还要调 LLM 抽取，运行时依赖无法收敛 |
| Graphmem / sqlite-graph-memory / Mnemoverse / Selvedge / Synapse | MIT | 能拷，但等于把它的存储格式、它的 schema 迁移变成本仓的维护责任 |

许可只说一句关键的：**MIT 仓里混进 AGPL-3.0 代码，整仓的分发义务就跟着变**（AGPL 连"通过网络提供服务"都算分发场景）。
所以不拷代码、不拷数据格式，只走**进程边界 + 协议边界**：外部服务怎么实现、用什么许可、装多少依赖，都由它自己负责。

结论落成三条本仓规则：

1. 本仓只用标准库，CI 里"没有第三方依赖"是硬事实，不是口号；
2. 外部服务的崩溃、超时、重启**不是指针的错误**，体检不得因此报断链；
3. 写入语义仍归外部服务（`store://` 只做只读存在性校验，不做写入、不做同步）。

## 2. `store://` 怎么配

指针写法与其他 scheme 一致，只是 target 是"外部记忆对象 id"，不再是可以 `os.path.exists` 的东西：

```
deploy_notes = store://note-42 | 部署注意事项，含三个环境差异
embed_arch   = store://graph:arch-decisions | 架构决策图入口
```

规则表 `skills/memory-pointer-system/scripts/mem_rules.json` 里加两处（本仓已内置，默认关闭）：

```json
  "schemes": {
    "store": { "verify": "store_ref", "autofix": [], "report": "red" }
  },
  "store": {
    "_comment": "外部记忆服务对接（只对接不 vendor）：store:// 由外部服务判定对象是否存在。command 空=未配置（记 ? 待全量版，绝不报断链）；命令元素含 {target} 则替换，否则 target 追加到末尾。exit_map：0=命中 / 64=未找到（断链）/ 65=服务不可用（记 ? 不报断链）。timeout=单次判定秒数。",
    "command": [],
    "timeout": 20,
    "exit_map": {
      "0": "ok",
      "64": "missing",
      "65": "unavailable"
    }
  }
```

| 字段 | 含义 |
|---|---|
| `command` | 命令数组（**不是** shell 串，不经过 shell，所以管道/重定向/引号都不生效）。元素里的 `~` 与 `$VAR` 由引擎展开（与规则表其他路径字段同口径），相对路径按 mem_guard 的当前目录解析。空数组 = 未配置，此时所有 `store://` 记 `? 待全量版` |
| `{target}` | 命令元素里的占位符；出现则原位替换，不出现则把 target 追加为最后一个参数 |
| `timeout` | 单次子进程判定超时（秒）。超时 → `?`，消息带原因 |
| `exit_map` | 退出码 → 结论。未登记的退出码一律按"外部服务异常"记 `?`，不当断链 |

**默认关闭**的效果：`store://` 指针在没配 `command` 时全部记 `?`，体检报告只多一行
`? 待全量版 N`，不会有任何 🔴。想看到"为什么是 ?"，跑 `--verbose`，报告会追加一节
`? 待确认 N 条:` 逐条给出原因（未配置 / 服务不可用 / 超时 / 启动失败）。

## 3. 接入示例（都只把对方当外部进程/服务）

**通用姿势**：先用本仓的零依赖客户端探路，看对方暴露了哪些工具、参数长什么样，再把命令抄进规则表。

```bash
# ① 探路：initialize → tools/list（只打印，不写任何东西）
python examples/mcp_client_example.py --command "<对方 README 里的启动命令>"

# ② 试一次调用：把 ① 列出的工具名和参数填进来
python examples/mcp_client_example.py --command "<同一启动命令>" \
    --tool <工具名> --args '{"<参数>":"<值>"}'

# ③ 落进规则表前先单独验：命中 exit 0，未找到 exit 64，连不上 exit 65
python examples/mcp_client_example.py --command "<同一启动命令>" \
    --tool <工具名> --args '{"id":"{target}"}' --probe note-42
```

> 具体包名、启动命令、工具名一律以对方 README 为准，本文不替它猜——猜错的命令会让体检看起来"服务不可用"，
> 那种假故障比断链更难查。
>
> **两层占位符别混用**：`mem_guard` 会把 `command` 里出现的每一个 `{target}` 都替换掉，而 `mcp_client_example.py`
> 自己也会替换 `--args` 里的 `{target}`。所以规则表里的标准写法是 **`--field <字段名>` + `--probe {target}`**，
> `--args` 里不要再写 `{target}`（否则 mem_guard 先替它填死，客户端就拿不到目标了）。
> 字段名按服务实际参数改：`id` / `query` / `path` / `node` 都有可能。

### 3.1 Graphmem（MIT，stdio MCP，本地进程）

它是标准的 stdio MCP 服务，最省事的接法是让本仓客户端当适配层，`store.command` 里直接放三层调用：

```json
"command": [
  "python", "examples/mcp_client_example.py",
  "--command", "<Graphmem README 的启动命令，如 npx -y <pkg> 或 uvx <pkg>>",
  "--tool", "<它列出的查询工具名>",
  "--args", "{}",
  "--field", "id",
  "--probe", "{target}",
  "--timeout", "15"
]
```

链路：`mem_guard` → `mcp_client_example.py --probe` → Graphmem（stdio JSON-RPC）。
任何一环失败都只落到 65（`?`），不会变成 🔴 断链。

### 3.2 sqlite-graph-memory（MIT，本地库文件）

同一个套路，把启动命令换成它的入口即可：

```json
"command": ["python", "examples/mcp_client_example.py",
            "--command", "<sqlite-graph-memory 的启动命令>",
            "--tool", "<查询工具名>",
            "--args", "{}",
            "--field", "node",
            "--probe", "{target}"]
```

如果它本身带命令行查询子命令（不暴露 MCP），**跳过客户端直接用 CLI 更轻**：把 CLI 当 `command`，
让它自己对上退出码即可（见第 4 节的契约）。比如对方的命令在"找不到"时以 64 退出，就正好对齐。

### 3.3 Mem0 MCP（Apache-2.0，托管服务）

托管服务的差异只有两点：**要凭据**、**通常不是本地 spawn 而是 HTTP/SSE**。本仓只做两件事：

```json
"command": ["python", "examples/mcp_client_example.py",
            "--command", "<本地 stdio 桥接命令，或厂商给的 connector>",
            "--tool", "search_memories",
            "--args", "{\"user_id\":\"<你的 user id>\"}",
            "--field", "query",
            "--probe", "{target}",
            "--timeout", "30"]
```

- **密钥不进规则表**：`mem_rules.json` 是会被备份、被 diff 的受信配置，凭据只放环境变量（进程继承即可）；
- 托管服务的网络抖动非常常见，所以 `timeout` 给宽一点，并且**接受它经常是 `?`**——这正是 `unavailable` 这一档存在的理由。

### 3.4 自己写一个 probe 脚本（兜底，任何服务通用）

不想用客户端，写个 5 行的脚本就能当适配层，只要满足退出码契约：

```python
# probe_store.py 用法: python probe_store.py <target>
import sys, urllib.request, urllib.error
try:
    urllib.request.urlopen("http://127.0.0.1:8080/objects/" + sys.argv[1], timeout=5).read()
    sys.exit(0)                       # 命中
except urllib.error.HTTPError as e:
    sys.exit(64 if e.code == 404 else 65)
except Exception:
    sys.exit(65)                      # 连不上 = 服务不可用，不是断链
```

规则表写 `"command": ["python", "probe_store.py"]`（无占位符 → target 自动追加）。

### 3.5 一个可复制的实测样例（不依赖任何外部服务）

想先跑通链路再接真服务，用本仓 MCP 服务当"外部 store"（它只是一个普通 MCP 服务，恰好本机就有）：

```json
"command": ["python", "examples/mcp_client_example.py",
            "--command", "python mcp_server/pulse_path_mcp.py",
            "--tool", "pp_secrets_scan",
            "--args", "{}",
            "--field", "path",
            "--probe", "{target}"]
```

配一条存在的指针和一条不存在的指针，体检会分别给出 `✅ 正常` 和 `🔴 断链: store://<不存在>（外部 store 报未找到（exit 64））`。
把 `--command` 换成真服务的启动命令、`--tool/--field` 换成它的工具和参数名，就是一次真接入。

## 4. 退出码契约

| 退出码 | 含义 | 体检结论 |
|---|---|---|
| `0` | 对象存在 | ✓ 正常（计入"正常"数） |
| `64` | 服务明确回答"没有这个对象" | 🔴 断链，消息写明"外部 store 报未找到（exit 64）" |
| `65` | 服务不可用 / 连不上 / 认证失败 | `?` 待确认，**不报断链** |
| 其他（含超时、启动失败） | 未登记情况 | `?` 待确认，`--verbose` 里带原因 |

64/65 落在 `>63` 的应用自定义段（`sysexits.h` 约定的区段），与 shell/CI 里随处可见的 `0/1/2` 不冲突：
体检自己的退出码恒 0（问题只看 stdout），两者不会互相污染。

写自己的适配层时请守住三条：**只有"确定不存在"才 64**；一切网络/进程/解析故障都 65；不要用 1 表示不存在（会被当成未登记情况）。

## 5. 局限（先说清楚，别指望它兜）

1. **外部服务挂了 = `?`，不是"健康检查"**：本仓没有守护、没有重试、没有降级缓存。服务挂了你会看到一堆 `?`，这是设计意图（不误报断链），不是 bug。
2. **不做缓存**：每次体检都真查。指针条数 × `timeout` 是最坏耗时，`store://` 只适合少量高价值对象，不适合把几百条记忆全挂上去。
3. **不做批量**：一条指针一个子进程。真要跑批量，在适配层里自己批，别让 mem_guard 循环 spawn。
4. **不做写入 / 不做同步**：`store://` 只读校验；写入、去重、embedding、schema 迁移都归外部服务。
5. **`command` 是受信配置**：体检会执行它。`mem_rules.json` 等同于可执行代码，不要指向远端可写路径。
6. **相对路径按 mem_guard 的 cwd 解析**：定时任务/不同目录下跑会找不到脚本，配置里请写绝对路径或 `${HERMES_HOME}/...` 这类可展开写法。
7. **不做语义检索本身**：本仓不存向量、不调 embedding、不改写摘要。语义召回请在外部服务里做（见下一节分工）。

## 6. 与 `pp_index.py`（本地 FTS5）的分工

仓内有两个"找东西"的能力，边界是**检索类型**，不是数据量：

| | `pp_index.py` / MCP 工具 `pp_search` | `store://` + 外部服务 |
|---|---|---|
| 检索方式 | 本地 SQLite FTS5 全文索引（trigram，中文/英文子串都能命中；<3 字符回退 LIKE 并标注 `mode=like`） | 语义 / 向量 / 图检索，由外部服务定义 |
| 覆盖范围 | 指针背后的 L2 正文（`doc://` 文件、`skill://` 的 SKILL.md 与 references） | 外部记忆服务里的对象（笔记、图节点、episode…） |
| 依赖与联网 | 零依赖、离线（`stdlib sqlite3`），索引是 `$HERMES_HOME/.pp_index/` 下可随时重建的一次性缓存 | 需要外部进程/服务，可能联网、可能需要凭据 |
| 延迟 | 毫秒级 | 取决于外部服务，常见几十毫秒起 |
| 典型问题 | "我记得哪份文档里出现过『悬空指针』" | "哪些决策和这次架构变更语义相关" |

**用的时候按这个顺序**：

1. 先用本地关键词检索（`pp_search` / `pp_index.py --search`）找文档——离线、快、结果可回溯到具体文件；
2. 关键词想不出来（同义词、跨文档语义关联、图上的邻居）再交给外部服务；
3. 两条链路的产出都只是**线索**，指回 `MEMORY.md` 里的指针才算闭环——指针仍是唯一 SSOT，检索结果不是。

换句话说：`pp_index.py` 负责"**我记得文档里有个词**"，`store://` 负责"**我记得有过这么回事，但想不起用词**"。
两者都不替记忆做决定，只负责把细节找回来。
