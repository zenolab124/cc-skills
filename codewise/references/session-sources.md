# 会话来源与项目归属

## 目录

1. 统一发现命令
2. Git Worktree 项目身份
3. 内建来源
4. 内容级过滤
5. 会话提取协议
6. 扩展新来源
7. 限制与降级

## 统一发现命令

不要手写某个客户端的会话目录。始终先运行：

```bash
python3 <SKILL_DIR>/scripts/discover_sessions.py <ROOT> --pretty
```

增量更新时增加时间粗筛，并回传上次记录的 Worktree 集合：

```bash
python3 <SKILL_DIR>/scripts/discover_sessions.py <ROOT> \
  --since '<_sync.json 的 synced_at>' --baseline '<_sync.json 的 baseline_commit>' \
  --known-worktree '<_sync.json 的 known_worktrees 每一项>' --pretty
```

条目较多时改用文件形式，一行一个路径（`#` 开头为注释）：

```bash
python3 <SKILL_DIR>/scripts/discover_sessions.py <ROOT> \
  --known-worktrees-file '<临时清单文件>' --pretty
```

脚本只读轻量元数据并输出清单，不复制、改写或删除原始会话。`mtime` 只用于减少候选；进入清单的会话仍要从头到尾阅读，并在消息级使用 `synced_at` 作为提取边界。

输出中的关键字段：

- `project.worktrees[]`：同一 `git-common-dir` 下的所有 Worktree，以及每个 Worktree 对应的 scope 根。
- `project.worktrees[].stale`：为 `true` 表示该路径来自 `--known-worktree`，已不在 `git worktree list` 中。它只用于会话路径匹配，没有 `head`/`branch`/`merge_base`，因此其会话的 `branch_state` 必然是 `unknown-branch`，判定保持保守。
- `project.known_worktrees`：本次应持久化、下次原样回传的完整 Worktree 路径集合（当前活跃 ∪ 传入的历史）。主流程只负责存取，合并逻辑在脚本内完成。
- `relation`：会话 cwd/hash 与 scope 的关系。
- `needs_content_check`：是否必须依赖会话内容确认项目归属。即使为 `false`，子项目 scope 仍不得越界提取。
- `selector`：数据库型来源中的会话选择器；同一个数据库文件可对应多个会话。
- `branch` / `branch_source` / `branch_state`：会话**起始**的历史分支证据，以及它与扫描时 Worktree 分支的关系。
- `branches` / `multi_branch`：该会话记录过的全部分支（首次出现顺序）。`multi_branch=true` 表示会话被 resume 跨过分支切换——此时 `branch` 只代表开头那一段，**必须按段归属信号**，判定规则见 [branch-resolution.md](branch-resolution.md)。detached HEAD 时客户端会记字面量 `"HEAD"`，它不是分支，已被过滤掉。
- `session_head` / `head_state` / `scan_head` / `merge_base`：会话自身记录的提交（若来源提供）、它与发现时 Worktree HEAD 的关系、Worktree 当前 HEAD，以及它与当前知识库 baseline 的共同祖先；只用于比较，不代表会话发生时的完整工作树状态。

当 baseline 不存在、无法解析或某个 Worktree 没有共同祖先时，脚本会在 `warnings` 中报告；该 Worktree 的变更范围必须标为无法比较，不能用另一个 Worktree 的基线代替。

先向用户报告各来源与 Worktree 的候选数量，再开始精读。不要把绝对会话路径写进知识库条目。

## Git Worktree 项目身份

项目身份不是 cwd 字符串，也不是目录名。Git 仓库以以下结果为身份锚点：

```bash
git -C <ROOT> rev-parse --path-format=absolute --git-common-dir
git -C <ROOT> worktree list --porcelain
```

发现脚本把所有共享 `git-common-dir` 的 Worktree 视为同一项目，并将 `<ROOT>` 相对当前 Worktree 根的路径映射到其他 Worktree。例如当前 scope 是 `apps/web`，另一个 Worktree 的有效 scope 是 `<other-worktree>/apps/web`。

**Worktree 可以嵌套在主工作树内部**——`.claude/worktrees/<name>` 就是 Claude Code 自建 Worktree 的默认位置。因此 cwd 归属必须取**最深匹配**而不是第一个包含它的 Worktree：按第一匹配会把这些会话记到父工作树名下，分支判定错误，且 `needs_content_check` 会被错误清零而跳过内容验证。脚本已按路径深度降序匹配，扩展匹配逻辑时不要破坏这个顺序。

Claude Code 的项目目录名把路径里**所有非 `[A-Za-z0-9]` 字符**替换成 `-`（大小写保留），所以 `/x/p/.claude/worktrees/w` 对应 `-x-p--claude-worktrees-w`。只替换路径分隔符会漏掉一切含点的路径，恰好包含上述自建 Worktree 位置。

处理规则：

- 当前 Worktree 会话与其他 Worktree 会话都必须扫描。
- 会话里的绝对路径先映射为“Worktree 根 + 相对路径”，再映射回当前 `<ROOT>`；知识库只写 `<ROOT>` 相对路径。
- 会话自身带有 `gitBranch`、`git_branch` 或 `payload.git.branch` 等历史字段时，保存为 `branch_source=session-metadata`；只有扫描时 Worktree 分支而没有历史字段时，标记为 `branch_source=unknown`，不能反推会话当时的分支。
- 其他 Worktree 的改动只是候选事实。只有当前 `<ROOT>` 的代码或当前分支 git 历史能验证时，才可写成当前架构事实。
- 未合并、已回滚、已删除 Worktree 中的讨论，可进入 `decisions/` 作为演变记录；不得把分支专属实现写进 `domains/`、`shared/`、`integrations/`、`workflows/` 或当前有效的 `pitfalls/`。

**已删除 Worktree 的会话打捞：** `git worktree remove` 与 `git worktree prune` 会把条目从注册表移除，此后该 Worktree 下的所有会话再也无法通过路径匹配发现——短期实验 Worktree 用完即弃，这类损失是静默且不可补救的。因此每次运行都必须把 `project.known_worktrees` 持久化，并在下次运行时通过 `--known-worktree` 回传：路径还在，slug/hash 就仍能算出，会话就仍然找得到。注意只删目录而不 prune 时注册表条目仍然存在，此时无需依赖该机制。

分支状态的安全含义：

- `session-branch-matches-scan`：会话历史分支与扫描时 Worktree 分支相同，但仍需用当前代码验证实现事实。
- `session-branch-differs-from-scan`：会话明确属于其他分支。**这不等于"未合并"**——分支名不同与是否已合并是两件事，必须按 [branch-resolution.md](branch-resolution.md) 的合并状态判定链定性，再按断言类型（状态 / 因果）分流。
- `unknown-branch`：没有历史分支证据；默认隔离，不能直接更新当前 `domains/`、`shared/` 或 `integrations/` 条目。

**各来源的分支记录粒度不对称，不要一视同仁：**

| 来源 | 字段 | 粒度 | 覆盖率 |
|---|---|---|---|
| Claude Code | `gitBranch` | 每条 user/assistant 消息 | 100%（本机实测） |
| Codex | `payload.git.branch` + `commit_hash` | 每次启动/resume 一次 | 约 87%（本机抽样） |
| 其他 | 通常无 | — | — |

Claude 的分段精度更高（消息级）；Codex 有 `commit_hash`，这是 Claude 没有的**最强合并证据**（commit 是否在 `baseline..HEAD` 历史中，一查即定）。判定时按来源取可得的最强证据，不要假设所有来源都能给出同一档位的结论。

**只有 Claude 与 Codex 会产出 `branches` 时间线**，其余来源的 `multi_branch` 恒为 `false`——那是"无分支字段"而非"未跨分支"，它们本就统一按 `unknown-branch` 保守处理，不要把恒 false 当成故障。

**会话归属优先看文件所在的项目目录，其次才看会话记录的 cwd。** Claude Code 按会话**启动时**的 cwd 决定项目目录，而元数据里取到的是文件中**第一个** cwd——会话中途进入 Worktree（或在 Worktree 中被 resume）时两者不一致，用第一个 cwd 会把整条会话判给父工作树，连带 `scan_branch`/`scan_head`/`merge_base` 全部取错参照物。`relation=scope-project-dir` / `worktree-project-dir` 表示归属来自目录证据；`scope-cwd` / `worktree-cwd` 表示来自 cwd 兜底。

## 内建来源

| 来源 | 常见存储 | 格式 | 项目归属 |
|---|---|---|---|
| Claude Code | `~/.claude/projects/<sanitized-cwd>/**/*.jsonl` | JSONL | 消息 `cwd` + Worktree 映射；同时覆盖 sidechain/subagent 文件 |
| Codex CLI / Desktop | `${CODEX_HOME:-~/.codex}/{sessions,archived_sessions}/**/*.jsonl` | rollout JSONL | `session_meta.payload.cwd` + Worktree 映射；不同 `source`/`originator` 一视同仁 |
| Gemini CLI | `~/.gemini/tmp/<sha256(project-root)>/chats/session-*.json` | JSON | Worktree/scope 绝对路径的 SHA-256 |
| OpenCode | `~/.local/share/opencode/opencode.db` 或 `storage/session/**/*.json` | SQLite / JSON | session 的 `directory`/`cwd` + Worktree 映射 |
| Cursor | `~/.cursor/projects/<project>/agent-transcripts/**` | 文本/JSONL（尽力发现） | 项目目录候选 + 强制内容过滤 |
| Aider | 每个 Worktree 的 `.aider.chat.history.md` | Markdown | 文件所在 Worktree |

脚本尊重客户端原生的 `CLAUDE_CONFIG_DIR`、`CODEX_HOME`、`GEMINI_CLI_HOME`，也可用 `CODEWISE_CLAUDE_HOME`、`CODEWISE_CODEX_HOME`、`CODEWISE_GEMINI_HOME`、`CODEWISE_OPENCODE_HOME`、`CODEWISE_CURSOR_HOME` 覆盖测试环境或非默认安装位置。

客户端内部格式会变化。若某个已安装来源候选数异常为 0，先检查实际目录与一份样本的结构，只更新对应适配器，不要绕过统一发现流程重新把路径写死进 `SKILL.md`。

## 内容级过滤

目录命中只代表“候选”，不能证明会话涉及 `<ROOT>`。逐会话按以下证据判定。

强证据，任一项可判为相关：

- 编辑、写入、补丁、删除工具的目标文件映射到 `<ROOT>` 内。
- Shell/tool cwd 在某个 Worktree 的 scope 内，且命令会修改文件、运行该 scope 的测试或查看该 scope 的 diff。
- 会话展示的 git diff、状态或提交文件位于 scope 内。
- 会话元数据 cwd 位于 scope 内，并且讨论/工具操作出现该项目的相对文件路径。

弱证据，不能单独判为相关：

- 只提到仓库名、技术栈或通用文件名（如 `package.json`）。
- cwd 仅位于 monorepo 根，而 `<ROOT>` 是一个子项目。
- 会话只读取外层 `CLAUDE.md`/`AGENTS.md`，没有操作 scope 内文件。
- 同名路径只存在于另一个仓库或另一个子项目。

不相关时返回“无关 + 一句判定依据”，不要继续提取。相关时记录证据所对应的 Worktree 与相对文件路径，供后续 git/当前代码交叉验证。

## 会话提取协议

不同来源先按各自格式阅读，再输出同一个归一化摘要：

```text
provider / session_id / parent_session_id(可得时) / worktree / branch / branch_source / branch_state / session_head / head_state
时间范围 / synced_at 后是否有有效活动
项目归属证据: [相对路径 + 操作类型]
改动结果: 已落地 | 未合并分支 | 已回滚 | 仅讨论 | 无法确认
涉及文件: [<ROOT> 相对路径]
任务与结果: 做了什么、最终结果
pitfalls 候选: 现象 / 错误假设 / 根因 / 规避
decisions 候选: 背景 / 候选方案 / 选择 / 原因
workflows 候选: 步骤 / 易漏点
```

格式读取提示：

- Claude：按 JSONL 原顺序读 `user`/`assistant`；工具调用在 `message.content` 中。不要只读最终回复。
- Claude：优先使用事件上的 `gitBranch` 作为历史分支证据；不要用当前 cwd 现在的分支替代它。
- Codex：按 rollout JSONL 原顺序读；用户/助手文本常见于 `response_item` 和 `event_msg`，工具与补丁事件也必须保留。以 `session_meta.payload.id` 作为独立 rollout/session id；`payload.session_id` 可能是多个子 Agent 共享的父线程 id，只用于关联，不能据此去重。若 `session_meta.payload.git.branch` / `commit_hash` 存在，分别保存为历史分支与历史提交；没有则保持未知，不用当前 cwd 的分支补齐。`session_meta` 只用于归属，不代表会话内容。
- Gemini：按 `messages` 数组原顺序读，保留工具调用与工具响应之间的关系。
- OpenCode JSON：按 session → message → part 的逻辑顺序读。SQLite 使用只读连接，按清单的 `selector` 取 session，再按时间/序号连接 message 与 part；先查看 `PRAGMA table_info`，不要假设版本字段恒定。
- Gemini/OpenCode：若会话元数据提供 `branch`/`gitBranch`/`git_branch` 与提交字段，保留为历史 provenance；没有则标记未知。
- Cursor/Aider/custom：保持原始顺序，识别用户、助手、工具/补丁块；无法可靠分角色时宁可标为原始记录，不要猜。

隐私边界：只把语义结论和相对路径写入知识库。不得复制原始用户消息、thinking、tool result 大段原文、密钥、令牌、邮箱或含用户名的绝对路径。

## 扩展新来源

文件型来源无需改脚本即可加入：

```bash
python3 <SKILL_DIR>/scripts/discover_sessions.py <ROOT> \
  --source 'continue=~/.continue/sessions/**/*.json' \
  --source 'kimi=~/.kimi/sessions/*/context.jsonl' \
  --pretty
```

自定义来源统一标记 `needs_content_check=true`。确认某来源长期稳定、能从元数据取 cwd/project id 后，才在 `discover_sessions.py` 新增内建适配器。适配器必须只负责：

1. 枚举候选会话；
2. 提取 session id、cwd/project id、mtime；
3. 映射 Worktree；
4. 输出统一清单。

不要让适配器直接生成知识条目；内容过滤、事实校验和隐私清洗必须留在主流程。

## 限制与降级

- Cursor 的 SQLite/内部 KV、Windsurf 的 protobuf、云端 Agent 会话等格式若无法可靠读取，明确报告“检测到但未自动解析”，请用户导出为 JSON/Markdown 后通过 `--source` 加入。不要静默宣称已覆盖。
- `mtime` 不是消息时间。增量扫描时文件通过 mtime 粗筛后，仍要完整阅读会话并只为 `timestamp > synced_at` 的讨论产出新信号。
- 正在写入的当前会话可能尚未完整落盘。当前会话由 Phase U.2 直接回顾；按 `provider + session_id` 去重，Codex 子 Agent 的独立 `payload.id` 必须保留。
- 数据库文件 mtime 只能筛数据库整体，不能筛单条会话。读取后必须按消息时间应用 `synced_at`。
- 会话数量极多时可以分批并行，但不能抽样、只读标题或只读最终回复。
- Git 只能列出注册表中的 Worktree。已从注册表移除的 Worktree 依靠 `--known-worktree` 回传才能保住其会话，但它们没有 `head`/`branch`，归属只能靠内容证据；同一路径后来切换过分支、以及没有历史分支字段的会话，同样不能仅凭 cwd 还原历史归属。此类会话默认隔离，需当前代码/HEAD 复核。
- 跨机器同步来的会话，其 Worktree 路径在本机往往无对应物（临时 Worktree 的路径是一次性的）。这类会话不做路径匹配，只走纯内容证据过滤。这是已知限制，不要假装能靠路径还原。
