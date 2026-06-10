#!/usr/bin/env bash
# SessionStart hook: 检测 VS Code Claude Code 扩展的思考块 patch 是否在位。
# 已 patch / 未装扩展 → 静默退出（快路径，毫秒级）；
# patch 丢失（扩展自动更新重写 extension.js）→ 自动跑 repatch，并以 systemMessage 提醒用户重启。
# 背景与手动修复流程见同目录 SKILL.md。
set -uo pipefail

EXT=$(ls -d "$HOME"/.vscode/extensions/anthropic.claude-code-*-darwin-arm64 2>/dev/null | sort -V | tail -1)
[ -z "${EXT:-}" ] && exit 0
F="$EXT/extension.js"
[ -f "$F" ] || exit 0

# 快路径：patch 在位，静默退出
grep -qE 'thinking-display",[A-Za-z_$]+\.display\|\|"summarized"' "$F" && exit 0

# patch 丢失 → 自动重打（repatch 脚本幂等、失败自动回滚，无人值守跑是安全的）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if OUT=$(bash "$SCRIPT_DIR/repatch-thinking-display.sh" 2>&1); then
  MSG="🩹 fix-thinking: 检测到扩展更新（$(basename "$EXT")）清掉了思考块 patch，已自动重打。Cmd+Q 完全退出 VS Code 再重开后生效（Reload Window 无效；当前会话可继续用，只是暂不显示思考块）。"
else
  MSG="⚠️ fix-thinking: 思考块 patch 丢失且自动重打失败——扩展 $(basename "$EXT") 可能改了内部结构。对 Claude 说「修思考块」，按 fix-thinking skill 的指引重新定位 patch 点。脚本输出: $OUT"
fi

MSG="$MSG" python3 <<'PYEOF'
import json, os
msg = os.environ['MSG']
print(json.dumps({
    "systemMessage": msg,
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "[fix-thinking SessionStart hook] " + msg,
    },
}, ensure_ascii=False))
PYEOF
