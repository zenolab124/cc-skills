---
name: recall
description: Search past Claude Code sessions for prior discussions, decisions, or code references. Use when the user says "回忆"、"上次"、"之前那个会话"、"上周我们"、"recall"、"remember when"、"what did we decide about", or otherwise references something from a previous session that isn't in current context or memory.
---

# recall — 跨会话回忆

在 `~/.claude/projects/**/*.jsonl` 中检索历史会话，返回匹配片段（含 ±3 条上下文）。

## 何时使用

用户提到过去某次会话里的内容，而当前上下文和 auto memory 里都没有时。典型信号：
- "上次我们怎么处理的 X"
- "之前那个会话里你说过 Y"
- "回忆一下关于 Z 的讨论"

**先检查 auto memory**（`~/.claude/projects/-Users-xt-workspace-cc-skills/memory/MEMORY.md`）；memory 是浓缩沉淀，命中更快。memory 不够再用 recall 翻原始会话。

## 用法

```bash
python3 ~/.claude/skills/recall/search.py <pattern> [options]
```

`<pattern>` 是大小写不敏感的正则。常用选项：

- `--current` 只搜当前 cwd 对应的会话
- `--project <substr>` 只搜 cwd 包含该子串的会话
- `--days N` 只搜最近 N 天
- `--context N` 上下文条数（默认 3）
- `--limit N` 最多返回的会话数（默认 20）

默认搜索所有项目所有时间。

## 输出格式

```
── session abc12345 · 2026-04-05 14:22 · cc-skills ──
  cwd: /Users/xt/workspace/cc-skills
    [user] 上一条非匹配上下文
  ▸ [assistant] 命中行
    [tool:Read] cc-skills/recall/SKILL.md
  ┄
```

`▸` 标记匹配所在的消息。工具调用以 `[tool:Name] 关键参数` 形式压缩显示（Read 显示路径、Bash 显示命令、Grep 显示 pattern 等）。工具输出被省略。

## 检索策略建议

1. **先窄后宽**：优先 `--current --days 30`，不够再去掉限制
2. **用 OR 正则** 一次搜多个候选词：`"unocss|atomic css|原子化"`
3. **找到线索后追问**：命中片段给出 session-id 前 8 位，可 `rg` 原始 jsonl 看完整上下文
4. **不要 dump 所有结果给用户**：读完后用自己的话总结，只在用户要求时贴原文

## 局限

- 只能关键词/正则匹配，不是语义搜索。想不起关键词时需要换多个同义词试
- 工具输出被省略，如果线索在 tool_result 里（比如某次 Read 的文件内容）搜不到
- 当前正在进行的会话（未落盘部分）不在索引内
