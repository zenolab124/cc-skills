---
name: recall
description: Search past Claude Code sessions for prior discussions, decisions, or code references. Use when the user says "回忆"、"上次"、"之前那个会话"、"上周我们"、"recall"、"remember when"、"what did we decide about", or otherwise references something from a previous session that isn't in current context or memory.
---

# recall — 跨会话回忆

在历史会话中检索关键词，返回匹配片段和上下文。

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

### 路径 B：search.py（fallback）

MCP 不可用时（非 CC Space 环境、MCP server 未连接）回退到本地 Python 脚本。

```bash
python3 ~/.claude/skills/recall/search.py <pattern> [options]
```

`<pattern>` 是大小写不敏感的正则。常用选项：

- `--current` 只搜当前 cwd 对应的会话
- `--project <substr>` 只搜 cwd 包含该子串的会话
- `--days N` 只搜最近 N 天
- `--context N` 上下文条数（默认 3）
- `--limit N` 最多返回的会话数（默认 20）

输出格式：
```
── session abc12345 · 2026-04-05 14:22 · cc-skills ──
  cwd: /Users/xt/workspace/cc-skills
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
- 工具输出被省略（search.py）或不索引（search_sessions 跳过 tool_result）
- 当前正在进行的会话（未落盘部分）不在索引内
