---
name: recall
description: Search past Claude Code and Codex sessions for prior discussions, decisions, or code references. Use when the user says "回忆"、"上次"、"之前那个会话"、"上周我们"、"Codex 里做的"、"Claude 里做的"、"recall"、"remember when"、"what did we decide about", or otherwise references something from a previous session that isn't in current context or memory.
---

# recall — 跨会话回忆

在 Claude Code 与 Codex 的历史会话中检索关键词，返回匹配片段和上下文。Claude Code 与 Codex 共用本技能。同一项目常在两个 Agent 间交替推进，用户说"刚才/昨天做的"时，默认两边都搜，不要只搜自己这一侧。

## 何时使用

用户提到过去某次会话里的内容，而当前上下文和记忆里都没有时。典型信号：
- "上次我们怎么处理的 X"
- "之前那个会话里你说过 Y"
- "回忆一下关于 Z 的讨论"

**先查自己的记忆**：记忆是浓缩沉淀，命中更快；不够再用 recall 翻原始会话。Claude Code 查当前项目的 `memory/MEMORY.md`，Codex 查 `~/.codex/memories`。

## 执行策略

### 主路径：search.py（两端通用）

覆盖 `~/.claude/projects` 与 `~/.codex/{sessions,archived_sessions}`。

```bash
python3 ~/workspace/cc-skills/recall/search.py <pattern> [options]
```

`<pattern>` 是大小写不敏感的正则。常用选项：

- `--current` 只搜当前 cwd 对应的会话
- `--project <substr>` 只搜 cwd 包含该子串的会话
- `--days N` 只搜最近 N 天
- `--context N` 上下文条数（默认 3）
- `--limit N` 最多返回的会话数（默认 20）
- `--source all|claude|codex` 检索哪个 Agent 的会话（默认 all）
- `--tools` 额外匹配工具调用摘要（命令、改动文件、MCP 调用）；默认只搜双方文字对话，查"当时跑了哪个脚本/改了哪个文件"时再开

Codex rollout 总量达数 GB，脚本先读首行 `session_meta` 按 cwd 过滤再全文解析；仍建议带 `--current` 或 `--days`。

输出格式：
```
── codex 01a0c311-cb52-… · 2026-09-21 08:29 · snap-ub ──
  cwd: /Users/xt/workspace/unibest/snap-ub
  file: /Users/xt/.codex/sessions/2026/09/21/rollout-….jsonl
    [user] 上一条非匹配上下文
  ▸ [assistant] 命中行
  ┄
```

### 加速路径：CC Space search_sessions（仅 Claude Code，可选）

当前会话存在 `mcp__cc-space__search_sessions` 时，可先用它搜 Claude 会话（Rust 全文索引，毫秒级，query 为空格分隔的 AND 关键词）。它**只索引 Claude Code 会话**；涉及 Codex 或无命中时，仍走主路径。

## 检索策略建议

1. **先窄后宽**：优先限定当前项目或近 30 天，不够再放宽
2. **多词覆盖**：search.py 用 `|` 正则（OR），search_sessions 用空格分隔（AND）
3. **找到线索后追问**：命中片段给出 jsonl 路径，可读原始 jsonl 的命中区段看完整上下文（含工具输出）
4. **不要 dump 所有结果给用户**：读完后用自己的话总结，只在用户要求时贴原文

## 局限

- 只能关键词匹配，不是语义搜索。想不起关键词时需要换多个同义词试
- 默认只保留用户与助手的文字对话：工具输出、思考块、图片始终不读；工具调用摘要需 `--tools`；上下文压缩摘要、技能注入、`<system-reminder>`/任务通知/IDE 与浏览器上下文等系统注入内容按白名单剔除
- Codex rollout 格式随版本演进：新版读 `event_msg/item_completed`，无该流时回退 `response_item`；Codex 升级后若命中骤减，先核对格式
- 两端都会实时写入当前会话，结果里可能出现正在进行的这次会话本身（检索词会命中自己），按 session id 或时间识别后忽略
