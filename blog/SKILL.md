---
name: blog
description: 博客文章写作与发布 — 根据主题/素材撰写博客文章，生成符合 AstroPaper 规范的 markdown 文件，保存到 cc-server/blog 并提交代码。Use when user says "blog", "/blog", "写博客", "写文章", "发博客", "write a blog post".
argument-hint: "[主题或内容描述]"
metadata:
  author: zeno
  version: "1.0.0"
---

# 博客文章写作与发布

根据用户提供的主题或素材，撰写一篇完整的博客文章，生成 markdown 文件并提交到博客仓库。

## 写作目标

$ARGUMENTS

---

## 第一阶段：素材准备

1. **理解主题**：分析用户提供的主题/素材/参考资料
2. **收集素材**：
   - 如果主题涉及代码库中的内容，阅读相关源码
   - 如果需要外部信息，使用 WebSearch 搜索最新资料
   - 如果用户提供了参考链接，使用 WebFetch 获取内容
3. **确定读者画像**：根据主题判断目标读者的技术水平

## 第二阶段：拟定大纲

向用户展示以下信息，**等待确认后再动笔**：

- **标题**（中文）
- **slug**（英文 kebab-case，用于 URL）
- **tags**（1-4 个，英文）
- **description**（1-2 句话摘要）
- **文章大纲**（二级标题列表，每个标题附一句话说明要写什么）

用户可能会：
- 调整标题/大纲结构
- 增减章节
- 要求换个角度
- 补充额外素材

灵活调整，直到用户满意。

## 第三阶段：撰写文章

### Frontmatter 规范

```yaml
---
author: zeno
pubDatetime: [当前 UTC 时间，ISO 8601 格式，如 2026-04-03T08:30:00Z]
title: [中文标题]
slug: [英文 kebab-case]
featured: false
draft: false
tags:
  - [tag1]
  - [tag2]
description: [1-2 句话摘要]
---
```

**字段说明**：
- `pubDatetime`：使用当前 UTC 时间，通过 `date -u +"%Y-%m-%dT%H:%M:%SZ"` 获取
- `slug`：如不指定则自动从文件名生成，但建议显式设置
- `featured`：默认 false，除非用户要求置顶
- `draft`：默认 false，如用户要求先存草稿则设为 true
- `tags`：使用英文，1-4 个，首字母大写

### 写作规范

**风格**：
- 默认使用中文撰写
- 语气自然，像是在跟同行聊技术，不要学术腔
- 开头直入主题，不要"随着 XXX 的发展"这类套话
- 结尾简洁有力，不要"总结一下/希望对你有帮助"

**结构**：
- 用 `##` 作为主要章节标题（h2），`###` 作为子标题（h3）
- 不要在文章开头重复 title（Astro 会自动渲染标题）
- 代码块标注语言类型
- 适当使用列表、表格增强可读性
- 长文章在开头可加一句话概括全文要点

**代码示例**：
- 代码要可运行，不要伪代码
- 关键代码加简短注释
- 避免超长代码块（超过 30 行考虑拆分或只展示核心部分）

**质量检查**：
- 确保技术内容准确
- 检查代码示例的正确性
- 确认没有前后矛盾的说法
- 文章长度适中（800-2000 字，视主题而定）

### 文件保存

- **路径**：`/Users/xt/workspace/cc-server/blog/src/data/blog/[slug].md`
- **文件名**：使用 slug 值，kebab-case
- 如果有配图需求，图片放在 `/Users/xt/workspace/cc-server/blog/src/assets/images/` 下

## 第四阶段：用户审阅

1. 文章写完后，告知用户文件路径
2. 提示用户可以：
   - 在编辑器中预览
   - 运行 `cd /Users/xt/workspace/cc-server/blog && npm run dev` 在浏览器中查看效果
3. **等待用户反馈**，根据意见修改
4. 可能需要多轮迭代

## 第五阶段：提交代码

用户确认文章内容后：

1. 在 `/Users/xt/workspace/cc-server/blog` 目录下操作 git
2. `git add` 新增的文章文件（及配图，如有）
3. commit message 格式：`post: [文章标题简写]`
   - 示例：`post: Claude Code hooks 机制详解`
4. **不主动 push**，除非用户明确要求

---

## 核心原则

- **先对齐再动笔** — 大纲必须经过用户确认
- **内容为王** — 技术准确性 > 文采 > 格式
- **不注水** — 宁可短小精悍，不要为了凑字数而啰嗦
- **尊重原创** — 如引用外部内容需注明来源
- **一篇一提交** — 每篇文章独立 commit，不混合其他改动
