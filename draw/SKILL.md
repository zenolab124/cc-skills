---
name: draw
description: >
  本机生图能力（Inkast API）。Use when the user expresses intent to generate, draw, or create
  an image in conversation — e.g. "画一张", "帮我生成", "draw me", "make an image of",
  "来张图", "生成一个海报", or when the conversation naturally leads to producing a visual.
  NOT triggered by discussing images abstractly or analyzing existing images.
---

# 生图能力 — Inkast 本机 API

通过本机常驻的 Inkast API (`http://localhost:21731`) 生成图片。

## 工作流程

### 标准流程（散文 → 图）

```
用户描述 → draft-prompt → 智能判断参数 → generate job → 轮询 → 下载 → 返回路径
```

**步骤 1：散文转结构化 prompt**

```bash
curl -s http://localhost:21731/api/draft-prompt \
  -H 'Content-Type: application/json' \
  -d '{"input":"<用户描述>","lang":"zh"}'
```

响应含 `prompt`（结构化 JSON）和 `hints`（消歧义建议）。

**步骤 2：提交生图任务**

```bash
curl -s http://localhost:21731/api/jobs/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":<步骤1的prompt>,"size":"<尺寸>","quality":"<质量>"}'
```

响应返回 `jobId`。

**步骤 3：轮询等完成**

```bash
curl -s http://localhost:21731/api/jobs/<jobId>
```

每 3 秒轮询一次。`status` 为 `succeeded` 时取 `generationId`。

**步骤 4：下载图片**

```bash
curl -L http://localhost:21731/api/generations/<generationId>/image -o <输出路径>
```

输出路径统一放 `~/Pictures/inkast/`，文件名用简短语义命名（如 `cyberpunk-tokyo-rain.png`）。

## 参数智能判断

不要写死参数，根据用户描述和对话上下文推断：

**尺寸**：
- 描述偏横向场景（风景、街景、全景）→ `1536x1024`
- 描述偏竖向（人像、海报、手机壁纸）→ `1024x1536`
- 无明显偏向或方形内容（头像、icon）→ `1024x1024`
- 用户明确说了比例就用对应值（16:9 → `1920x1080`，9:16 → `1080x1920`）

**质量**：
- 默认 `high`
- 用户说"快速"、"草稿"、"sketch" → `low` 或 `medium`

**格式**：
- 默认 `png`
- 用户要照片质感或提到压缩 → `jpeg`

## 交互准则

- **不要** 在生图前展示 API 调用细节或 JSON
- 生图需要 10-30 秒，提交任务后告知用户正在生成
- 生成完成后用 `SendUserFile` 把图片推送给用户（同时文件保留在 `~/Pictures/inkast/` 供后续使用）
- 如果 draft-prompt 返回的 hints 里有关键消歧义点（构图、视角等），**可以** 在生图前简短追问，但不要每次都问——只在描述确实模糊时才问
- 如果用户在对话中逐步细化需求（"再亮一点"、"换个角度"），基于之前的 prompt 修改后重新生成
- 失败时检查 errorMessage，给用户说人话，不要暴露原始错误

## 直接构造 prompt（高级）

如果对话中已经积累了足够的结构化信息，可以跳过 draft-prompt，直接构造 `ImagePrompt`：

```typescript
{
  type: string;       // photography / illustration / 3d-render / ...
  style: string;      // 风格
  subject: string;    // 主体
  background?: string;
  layout?: string;
  text_elements?: Array<{content: string, position?: string, font?: string, color?: string, size?: string}>;
  lighting?: string;
  mood?: string;
  camera?: string;
  color_palette?: string[];
}
```

至少需要 `type`、`style`、`subject` 三个字段。

## 服务异常处理

如果 API 不可达，尝试拉起：

```bash
launchctl kickstart gui/$(id -u)/com.inkast.api
```

等待 3 秒后重试。如果仍然失败，告知用户 Inkast 服务需要手动检查。
