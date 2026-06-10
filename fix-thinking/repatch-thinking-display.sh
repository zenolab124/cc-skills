#!/usr/bin/env bash
# 恢复 Claude Code VS Code 扩展的「思考块显示」patch。
#
# 背景:Opus 4.7+ 起 API thinking.display 默认 omitted,扩展又不传 --thinking-display,
#       思考摘要在 VS Code 不显示(#49902 / #59844)。showThinkingSummaries 对 IDE 无效。
# 本脚本:让扩展 spawn claude 时带 --thinking-display summarized
#         (只改 display、不碰 thinking.type,所以不会炸 WebFetch/WebSearch)。
# 何时跑:扩展每次自动更新会重写 extension.js、清掉 patch。更新后跑一次本脚本恢复,
#         然后 Cmd+Q 彻底退出 VS Code 再重开(Reload Window 不重启 claude 子进程,无效)。
#
#   bash ~/.claude/repatch-thinking-display.sh
#
set -uo pipefail

EXT=$(ls -d "$HOME"/.vscode/extensions/anthropic.claude-code-*-darwin-arm64 2>/dev/null | sort -V | tail -1)
[ -z "${EXT:-}" ] && { echo "✗ 未找到 Claude Code 扩展目录"; exit 1; }
F="$EXT/extension.js"
[ -f "$F" ] || { echo "✗ 未找到 $F"; exit 1; }
echo "扩展: $(basename "$EXT")"

# 幂等:已 patch 直接退出
if grep -qE 'thinking-display",[A-Za-z_$]+\.display\|\|"summarized"' "$F"; then
  echo "✓ 已是 patch 状态,无需重打。(若思考块仍不显示,Cmd+Q 彻底重开 VS Code)"
  exit 0
fi

# 目标行存在性检查(pattern-based,兼容不同版本的 minified 变量名)
if ! grep -qE 'if\([A-Za-z_$]+\.type!=="disabled"&&[A-Za-z_$]+\.display\)[A-Za-z_$]+\.push\("--thinking-display"' "$F"; then
  echo "✗ 没找到目标行 —— 新版本可能改了结构。把这句话发给 Claude 重新定位。"
  exit 1
fi

cp "$F" "$F.bak-thinkpatch"
perl -i -pe 's/if\((\w+)\.type!=="disabled"&&\1\.display\)(\w+)\.push\("--thinking-display",\1\.display\)/if($1.type!=="disabled")$2.push("--thinking-display",$1.display||"summarized")/g' "$F"

if grep -qE 'thinking-display",[A-Za-z_$]+\.display\|\|"summarized"' "$F" && node --check "$F" 2>/dev/null; then
  echo "✓ patch 成功 + JS 语法合法"
  echo "→ 现在 Cmd+Q 完全退出 VS Code 再重开,思考块即恢复"
else
  cp "$F.bak-thinkpatch" "$F"
  echo "✗ patch 失败或语法错误,已回滚。把这句话发给 Claude。"
  exit 1
fi
