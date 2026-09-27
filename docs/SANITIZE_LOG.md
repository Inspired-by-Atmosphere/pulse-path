# SANITIZE_LOG

Per repository rule: record the **category**, the **count**, and the **action taken**.
Raw values are never recorded here — not even hashed.

Target revision: staging superset of the previous public revision of this repository.
Scope: `README.md`, `skills/**` (docs, references, engines), `.gitattributes`, examples.

| 类别 | 条数 | 处理动作 |
|---|---|---|
| 用户目录绝对路径（`C:\Users\<user>\…`、`/c/Users/<user>/…`） | 27 | 删除，改为环境变量 `HERMES_HOME` / 对应脚本的 `*_MEMORY` `*_SKILLS` `*_REPORT` 等，默认值用 `~/.hermes` 与仓库内相对路径 |
| 项目盘绝对路径（其他盘符的开发/验证/归档目录） | 9 | 删除，改为 `--zone` / `--search-root` 命令行参数或规则表里的空配置（`enabled: false`），并在文档里说明由使用者自行填写 |
| 私有仓库 / 镜像地址（自建私有库、镜像库名、账号路径） | 4 | 删除，改为"权威库 / 本地 clone / 使用副本"的通用三副本描述；不再出现具体托管地址 |
| 组织与赛事名（学校/团队/竞赛/项目代号） | 13 | 删除或替换为中性占位（"某项目"、"项目甲"、"赛事"），案例小节标题改为"实战实例（匿名化）" |
| 真实姓名 / 成员称呼 / 账号昵称 | 4 | 删除；正文改为"用户""成员称呼"等通用说法 |
| 内部纪律条文与团队口径（口径替换表、答辩约束等具体条目） | 6 | 删除具体数值与条目，仅保留"从 SSOT 抽取替换表、所有子代理共用同一张表"的方法描述 |
| 未公开的内部数字（水位百分比、条目数、断言数、实测耗时/占用） | 18 | 删除或改为量级表述（如"降 30-40 个百分点""亚秒级"）；确需保留的数字改为用户可自配的阈值 |
| 内部工程/系统代号与内部路径片段（治理系统名、员工工作区、上传区、内部运维目录） | 11 | 删除，改为通用角色描述（"治理引擎""报告目录 `$HERMES_HOME/reports/`"） |
| 调度任务 ID（cron 形 ID） | 14 | 删除，改为"每日/每周 + 角色"的描述性说法（ID 属本机实例标识，非通用信息） |
| 私有通信渠道名（即时通讯投递目标） | 5 | 改为"通知渠道 / 上报"，不出现具体平台 |
| 第三方凭据键名（本机 .env 里实际存在的键名） | 2 | 删除，改为 `cfg://SOME_KEY` 形式的示例占位 |
| 硬件型号 / 本机设备标识 | 4 | 删除，改为"主模型""小 embedding 模型""消费级 GPU"这类通用说法 |
| 内外网 IP / 主机名 / MAC | 0 | 本仓未发现（S1 扫描确认零命中） |
| 凭据 / 私钥 / 令牌 / 邮箱 | 0 | 本仓未发现（S1 扫描确认零命中） |
| 已提交的构建产物（`__pycache__/*.pyc`） | 1 | 删除文件，并加入 `.gitignore` |
| 悬空引用（`SKILL.md` 引用仓库内不存在的 references 文件） | 2 | 补入对应文件（新增 `ghost-scan-v2.md`、`governance-engines.md`），消除断链；另一处指向本机私有文件的引用删除 |

## 署名与元数据

| 类别 | 条数 | 处理动作 |
|---|---|---|
| 作者署名不一致（上一版用了缩略昵称） | 1 | 统一为仓库规范署名 `Inspired-by-Atmosphere`（`LICENSE`、README） |
| 真实邮箱 | 0 | 未出现；`LICENSE` 用署名，`scripts/check_secrets.py` 对 noreply 地址放行 |

## 保留说明

- **中文正文保留**：三个技能的正文语言是该产品的一部分，未翻译；仅移除其中的个人/团队/内部信息。
- **日期保留**：文档里的技术日期（如"实测发现于某日"）不含个人或组织信息，保留以体现结论来源；
  涉及内部成绩/成果的日期与名次一并删除。
- **第三方引用保留**：`README` 与技能正文里引用的公开项目（claude-mem、Zettelkasten、
  broken-link-checker、OpenViking 等）均为公开信息，保留出处以便复用。

## 未处理 / 待决策

- 语义库（OpenViking 式）相关表述保留为可选集成，默认路径 `~/.openviking/...` 是**第三方工具的
  公开默认目录**，不含个人信息；如需彻底去除，见 `docs/DIFF_PLAN.md` 的待决策项。
- `scripts/check_secrets.py` 自身的正则里含 `password` / `token` / `Users` 等**词形**（不含任何真实值）。
  敏感字反查（门禁 S5）需把该文件列入白名单，理由见 `docs/DIFF_PLAN.md` §5。
