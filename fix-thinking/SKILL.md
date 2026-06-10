---
name: fix-thinking
description: 修复 VS Code Claude Code 扩展不显示思考块的问题（repatch --thinking-display）。Use when thinking blocks/summaries stop showing in the VS Code extension, when the SessionStart auto-repatch hook reports failure, or user says "修思考块"、"思考块不见了"、"思考块不显示"、"fix thinking"、"repatch thinking display".
---

# fix-thinking — VS Code 思考块显示修复

## 根因（背景知识）

- Opus 4.7+ 起 API 的 `thinking.display` 默认 omitted；VS Code 扩展 spawn `claude` 子进程时不传 `--thinking-display`，思考摘要在 IDE 不渲染（上游 issue #49902 / #59844）。`showThinkingSummaries` 设置对 IDE 集成无效。
- 本地解法：patch 扩展的 `extension.js`，让 spawn 参数带 `--thinking-display summarized`。只改 display，**不碰 `thinking.type`**——历史教训：用 `CLAUDE_CODE_EXTRA_BODY` 注入 thinking 会炸 WebFetch/WebSearch，勿用。
- 扩展每次自动更新会重写 `extension.js`，patch 即丢失，需要重打。

## 自动化现状

同目录 `check-thinking-patch.sh` 已注册为全局 SessionStart hook（`~/.claude/settings.json`）：每次会话启动检测 patch 是否在位，丢失则自动重打并提醒用户重启 VS Code。多数情况用户无感；本 skill 手动触发只用于 **hook 报告失败** 或 **用户主动要求** 时。

## 手动修复流程

1. 跑 `bash ./repatch-thinking-display.sh`（幂等、失败自动回滚）
2. 按输出分支处理：
   - **✓ patch 成功** → 告知用户 **Cmd+Q 完全退出 VS Code 再重开**（Reload Window 不重启 claude 子进程，无效）
   - **✓ 已是 patch 状态** → patch 没丢。先确认用户打 patch 后是否真正 Cmd+Q 重启过；重启过仍不显示则问题不在 patch（检查是否多版本扩展目录共存、最新目录是否与运行中版本一致等）
   - **✗ 没找到目标行** → 新版扩展改了内部结构，按下节重新定位

## 新版扩展重新定位 patch 点

1. 定位最新扩展：`ls -d ~/.vscode/extensions/anthropic.claude-code-*-darwin-arm64 | sort -V | tail -1`，目标文件 `extension.js`（minified 单行，用 `grep -o` 看局部上下文）
2. 搜 `thinking-display` 附近代码，未 patch 的原始形态（变量名随版本变化）：
   `if(X.type!=="disabled"&&X.display)Y.push("--thinking-display",X.display)`
3. patch 目标形态（display 为空也强制传 summarized，不动 type 判断）：
   `if(X.type!=="disabled")Y.push("--thinking-display",X.display||"summarized")`
4. 若结构有变，同步更新 `repatch-thinking-display.sh` 里的三处正则（幂等检查、目标行检查、perl 替换），重跑脚本
5. 验证：`node --check extension.js` 通过且能 grep 到 patch 特征；失败用 `extension.js.bak-thinkpatch` 回滚
6. 提醒用户 Cmd+Q 重启；把新形态更新回本 SKILL.md 和脚本注释

## 验证 patch 是否在位

```bash
grep -qE 'thinking-display",[A-Za-z_$]+\.display\|\|"summarized"' <扩展目录>/extension.js && echo 已patch || echo 未patch
```
