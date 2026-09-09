---
name: windows-remote
description: 从 Mac 远程操作 Windows，或让 Mac Agent 给 Windows Codex Agent 派发任务、读取进度与回复、在同一会话追问并取回证据。Use when the user asks for Mac-to-Windows control, Windows-side investigation, or collaboration between agents on the two machines.
---

# Mac 与 Windows 协作

Mac 保留主任务与判断上下文；Windows Agent 在自己的项目目录读取约束、调用本机工具、调查和执行已授权任务，并返回可核查证据。**优先使用已经配对的 Windows 桌面客户端任务**，以保留目标主机的 Computer Use、浏览器、插件和现有上下文。CLI 桥接作为命令任务或客户端不可用时的备用入口，不能把 CLI 能对话等同于客户端桌面能力已接通。

## 原生客户端协作（优先）

1. 用客户端工具 `list_threads` 和 `list_projects` 发现远端。以返回的 `hostId`、Windows 工作路径和任务内容确认身份，不猜主机 ID。远端任务可能已经出现在 `list_threads`，但 `list_projects` 仍只列本机；不能仅据后者判定未连接。
2. 用 `read_thread` 读取目标任务最近状态，确认用户意图和本轮范围。不得因任务旧标题或旧指令含“跑 100 轮”等目标而自动恢复历史工作。
3. 用户已指定或当前协作范围已明确的任务，通过 `send_message_to_thread` 发送本轮目标和验收边界，保留原样 `hostId + threadId`。省略模型参数以沿用对端配置。仅用户明确要求新建任务时才用 `create_thread`；不要为普通子任务擅建侧栏任务。
4. 用 `wait_threads` 等待进展，保存 cursor 并在后续传 `afterCursor`；单次等待不超过 60 秒。需要核查实际工具调用或最终回复时再用 `read_thread`，不要反复拉完整长历史。
5. 桌面能力验收须让 Windows Agent **实际调用 Computer Use** 查看一个已授权窗口，并回报工具名、观察结果及截图是否成功。命令行截图、插件“已安装”或普通文字回复都不能代替这项验收。需要额外应用授权时由用户完成，不自动操作 Codex 自身或绕过应用策略。
6. 继续追问同一对 `hostId + threadId`。派发响应不明或超时时，先查询目标任务是否已经收到消息，不能直接重发或切 CLI 造成双重执行。

这条路径依赖桌面客户端的“设置 → 连接 → 控制此 PC / 控制其他设备”配对；普通 SSH 连接不是等价替代。若未发现远端，明确检查客户端配对状态与当前工具可见性。官方说明见 [远程连接](https://learn.chatgpt.com/zh-Hans/docs/remote-connections)。

## CLI 备用桥接

入口：本 skill 下的 `scripts/windows_remote.py`（下文记作 `$BRIDGE`）。用 Python 3.9+ 执行。脚本自带 macOS launchd 传输，自动绕过 Loon 的应用进程树局域网限制，不要再次套 `lan`。SSH 主机须已配置并信任；默认 alias 为 `PC`。

1. 运行 `python3 "$BRIDGE" doctor --host PC --cwd '<Windows 项目绝对路径>'`，确认 CLI、Python、登录状态与桌面会话。缺依赖时按 [运行环境](references/runtime.md) 处理，不读取或复制登录凭据。
2. 把本轮任务写进 UTF-8 文件。说明目标、Windows 工作目录、已授权动作、输出证据和验收边界；不要默认复制整个 Mac 对话。Mac 的临时事实、未提交改动或最新决定不会自动出现在 Windows，应显式传递必要部分。
3. `python3 "$BRIDGE" start --host PC --cwd '<Windows 项目绝对路径>' --prompt-file '<任务文件>'` 返回 `runId`。默认只读；用户已授权修改时可用 `--sandbox workspace-write`。模型沿用 Windows 配置，不擅自选低一档模型。
4. 用 `status <runId>` 读取进度，或 `wait <runId> --seconds 45` 等待一段时间。单次等待最多 55 秒；保持主任务进度说明。SSH 结束不会结束 Windows 工作。
5. 完成后 `collect <runId>` 取回 `reply.md`、`events.jsonl`、`stderr.log`。本地结果在 `~/.codex/windows-remote/runs/<runId>/`；必须阅读实际回复再形成结论。`completed` 仅表示这轮 Agent 正常结束，不代表其声称的业务结果已通过验收。
6. 需要追问时，`reply <上轮 runId> --prompt-file '<追问文件>'`。它复用**同一个 Windows threadId**，返回新的 runId。每个 runId 表示一轮请求；不要用 `--last` 猜会话，不接管用户现有的活动任务。运行中的一轮要先等待结束；需要中止时用 `cancel <runId>`，随后读取状态确认。

把 Windows 回复当作有证据支撑程度的协作结论。源码字段定义、磁盘快照、进程内存与截图分别说明证明范围。需要查游戏当前数值时，让 Windows Agent 自己选择适用本机探针并返回时间、来源、路径和关键结果，不能用脚本成功退出或类型定义冒充现场验证。

CLI 备用桥接使用官方 Codex CLI 的独立持久会话；它不继承 Windows Codex App 任务专属的插件、动态工具或尚未结束的对话。若任务依赖这些能力，优先原生客户端路径，并以对端实际工具调用验收。

## 命令与文件

- `exec --host PC --script-file '<本地 UTF-8 PowerShell 文件>' --timeout 45`：运行明确的 Windows 命令并返回 stdout/stderr/退出码；这是 SSH 会话，不是桌面 GUI 会话。
- `put --host PC --local '<本地文件>' --remote '<Windows 绝对文件路径>'`：上传文件。
- `get --host PC --remote '<Windows 绝对文件路径>' --local '<本地文件>'`：取回日志、截图等指定证据。只取与任务相关的明确路径。

GUI 操作需要 Windows 已登录的桌面会话。CLI worker 虽在当前用户的一次性 Interactive 计划任务中启动，也不保证获得 Computer Use；原生客户端路径应由 Windows 本机工具完成窗口交互。锁屏时不擅自解锁。输出 UTF-8 文件后，Windows PowerShell 5 必须显式 `Get-Content -Encoding UTF8`，避免中文损坏。

## 项目适配与授权边界

通用通信不包含项目业务规则。SNAP-UB 已有 `scripts/remote_windows.py` 的 `status / sync / test / diagnose`，仍可用于确定性的版本核对、快进同步、固定测试与窗口取证；具体约束见项目 `docs/pc-script-automation.md`。复杂问题改为派给 Windows Agent，不局限于这四个动作。

用户对当前任务的授权同时约束两端。不要因“远程协作”自动构建、部署、重启游戏、安装 Hook、开始对局或扩大成子 Agent 审查。需要额外能力时先完成允许范围内的调查并准确回报阻塞原因；不得自动切到无沙箱模式。

`cancel` 通过任务专属标记停止本轮 Agent 进程树，不终止其他 Codex 或游戏进程。`failed / timed_out / cancelled / worker_unavailable` 都不能当作成功；保留 runId 和证据，先判断是否有未完成副作用，再决定追问或重试。每轮任务完成会移除自己的计划任务；会话与证据保留以便追问，清理时只删除已结束且已确认不再需要的本桥接 run 目录。
