#!/bin/bash
# Shared Claude/Codex adapter; keep both installed hooks identical to this file.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENTRY="$SCRIPT_DIR/session_start.py"
if [ ! -f "$ENTRY" ]; then
  ENTRY="${CODEWISE_SKILL_DIR:-$HOME/.claude/skills/codewise}/scripts/session_start.py"
fi
if [ ! -f "$ENTRY" ] || ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Codewise 导航脚本不可用；未读取任何知识内容，请按项目规则定位资料。"}}'
  exit 0
fi
exec python3 "$ENTRY" "$@"
