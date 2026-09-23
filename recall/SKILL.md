---
name: recall
description: Search past Claude Code and Codex sessions for prior discussions, decisions, or code references. Use when the user says "回忆"、"上次"、"之前那个会话"、"上周我们"、"Codex 里做的"、"recall"、"remember when"、"what did we decide about", or otherwise references something from a previous session that isn't in current context or memory.
---

# recall — 跨会话回忆

在 Claude Code 与 Codex 的历史会话中检索关键词，返回匹配片段和上下文。同一项目常在两个 Agent 间交替推进，用户说"刚才/昨天做的"时不要默认只在 Claude 会话里。

## 何时使用

用户提到过去某次会话里的内容，而当前上下文和 auto memory 里都没有时。典型信号：
- "上次我们怎么处理的 X"
- "之前那个会话里你说过 Y"
- "回忆一下关于 Z 的讨论"

**先检查 auto memory**（当前项目的 `memory/MEMORY.md`）；memory 是浓缩沉淀，命中更快。memory 不够再用 recall 翻原始会话。

## 执行策略（两条路径，按顺序尝试）

### 路径 A：MCP search_sessions（优先）

CC Space 提供的 `search_sessions` MCP 工具——基于 Rust 全文索引，毫秒级返回干净文本 + 会话定位。

```
用 search_sessions 工具，query 填空格分隔的关键词（多词 AND 匹配，大小写不敏感，中文原生支持）。
```

**何时可用**：当前会话的 MCP 工具列表中存在 `mcp__cc-space__search_sessions`。用 ToolSearch 搜一下即可确认。

**结果结构**：每命中一个会话返回 sessionId、projectId、title、KWIC 片段（含 uuid 和角色）、jsonlPath。需要更多上下文时，按 jsonlPath 直接读 jsonl 的命中区段。

**只索引 Claude Code 会话**。用户提到 Codex，或 MCP 无命中时，继续走路径 B（默认同时搜两边）。

### 路径 B：search.py（Codex 必经 / MCP 不可用时的 fallback）

覆盖 `~/.claude/projects` 与 `~/.codex/{sessions,archived_sessions}`。

```bash
python3 ~/.claude/skills/recall/search.py <pattern> [options]
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
    [tool:Read] cc-skills/recall/SKILL.md
  ┄
```

## 检索策略建议

1. **先窄后宽**：优先限定当前项目或近 30 天，不够再放宽
2. **多词覆盖**：search_sessions 用空格分隔（AND），search.py 用 `|` 正则（OR）
3. **找到线索后追问**：命中片段给出 session-id 和 jsonl 路径，可读原始 jsonl 看完整上下文
4. **不要 dump 所有结果给用户**：读完后用自己的话总结，只在用户要求时贴原文

## 局限

- 只能关键词匹配，不是语义搜索。想不起关键词时需要换多个同义词试
- search.py 默认只保留用户与助手的文字对话：工具输出、思考块、图片始终不读；工具调用摘要需 `--tools`；上下文压缩摘要、技能注入、`<system-reminder>`/任务通知/IDE 上下文等系统注入内容按白名单剔除。search_sessions 跳过 tool_result
- Codex rollout 格式随版本演进：新版读 `event_msg/item_completed`，无该流时回退 `response_item`；Codex 升级后若命中骤减，先核对格式
- 当前正在进行的会话（未落盘部分）不在索引内
