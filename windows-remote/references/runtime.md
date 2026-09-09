# 运行环境与故障定位

只在 doctor 失败或需要换机器时读本页。

当前已验证的目标是 SSH alias `PC`，Windows 用户资料目录为 `C:\Users\qazws`。项目路径只是调用参数，不是桥接依赖。

Windows 需要同一用户的可用 Python 3.9+、官方 Codex CLI 和已经登录的桌面。`doctor` 优先查 Codex 自带 Python，其次查指定项目的 `src-tauri/target/automation-runtime/python/python.exe`，再查 PATH 中的真实 Python；不会执行 WindowsApps 的 Python 商店别名。

官方 CLI 安装在隔离目录 `%LOCALAPPDATA%\MacWindowsBridge\cli`。需要初始化时，用已有 Node.js/npm 执行 `npm install --prefix <该目录> --no-save --no-audit --no-fund @openai/codex`；入口在 `<该目录>\node_modules\.bin\codex.cmd`，不是目录根。2026-09-09 实测版本为 `0.153.4`；不要把此版本号当作今后必须安装的版本。升级后重新验证 `exec --json` 和 `exec resume`。

Windows 商店版应用 `WindowsApps\OpenAI.Codex_*\app\resources\codex.exe` 曾在 SSH 和同用户 Interactive 任务中都返回 `Access is denied`。不要更改 WindowsApps ACL 或系统策略；独立官方 CLI 可以复用本机既有 ChatGPT 登录。`codex login status` 将正常信息写到 stderr，PowerShell 5 的 `$ErrorActionPreference='Stop'` 配合 `2>&1` 会误报，doctor 已兼容。

底层采用 SSH/SCP，不开网络监听端口。Mac 的 `~/.codex/windows-remote/runs/<runId>/` 保存请求、Windows 路径映射、状态与取回结果；Windows 的 `%LOCALAPPDATA%\MacWindowsBridge\runs\<runId>\` 保存请求、worker、事件、回复、取消标记。每轮建立 `MacWindowsBridge-<runId>` 一次性计划任务，使用现有用户 Interactive / Limited 令牌。它负责使进程脱离 SSH 生命周期，不提升为管理员。

worker 用 `codex exec --json --output-last-message ... -` 从 stdin 接收任务；后续用明确 threadId 的 `exec resume`。不手工解析或修改 Codex 的私有会话数据库。`started` 原子标记防止同一投递重复执行；同一 threadId 的 lease 防止两轮同时续写。worker 完成会清 lease 并尝试删除计划任务；Windows Limited 令牌可能没有删除 SSH 所建任务的权限，因此 Mac 的 `collect` 会再次清理，并回报 `temporaryTaskRemoved`。机器重启或进程异常消失会报告 `worker_unavailable`，先用任务/进程状态核实，不删除活跃 lease 或假装已完成。

2026-09-09 实测：两轮同 threadId 的中文任务和记忆追问成功，Windows Agent 实际运行 Git 和源码搜索；取消指定任务后进程退出，原桌面 Codex 保持运行；重复投递没有重跑。第二轮只读 Agent 调用 SNAP 的 `read_live_tracker_state(timeout_ms=500)` 收到 `PermissionError: [WinError 5]`，正常把失败与未验证边界回传。该实测没有证明沙箱中的 Agent 能直接读取目标命名管道或操作游戏窗口，也没有判定拒绝访问的具体原因；不要自动改系统 ACL 或取消沙箱。

参考：[官方非交互与恢复会话](https://learn.chatgpt.com/docs/non-interactive-mode)、[官方 CLI](https://learn.chatgpt.com/docs/codex/cli)。
