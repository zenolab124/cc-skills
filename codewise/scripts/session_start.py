#!/usr/bin/env python3
"""Emit bounded Codewise navigation only; never read knowledge/control contents."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_OUTPUT_BYTES = 4096


def git_path(cwd: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=3
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def regular_file(root: Path, relative: str) -> bool:
    if root.resolve() != root:
        return False
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            return False
    return current.is_file()


def has_entry(root: Path) -> bool:
    return any(regular_file(root, p) for p in (
        "docs/PROJECT_GUIDE.md", "docs/knowledge/INDEX.md", ".codewise-bootstrap.json"
    ))


def locate(cwd: Path) -> tuple[Path, Path] | None:
    """Choose nearest scope, mapping the same scope into a linked main worktree."""
    top = git_path(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return (cwd, cwd) if has_entry(cwd) else None
    root = Path(top).resolve()
    listing = git_path(root, "worktree", "list", "--porcelain", "-z")
    first = listing.split("\0", 1)[0] if listing else ""
    main = Path(first[len("worktree "):]).resolve() if first.startswith("worktree ") else root
    scope = cwd
    while True:
        relative = scope.relative_to(root)
        counterpart = main / relative
        if has_entry(scope) or has_entry(counterpart):
            return scope, counterpart
        if scope == root:
            return None
        scope = scope.parent


def message(cwd: Path) -> dict:
    located = locate(cwd.resolve())
    if located is None:
        return {}
    scope, main_scope = located
    lines = [
        "Codewise 仅提供按需检索入口；未读取索引或文档正文，未核验知识的分支归属与时效。",
        "当前实现查源码，决策/契约查相关权威文档，历史经验按需搜索旧库；无需为简单任务通读全库。",
        "遵守项目平台分工与跨端确认。普通 update 只维护受影响的权威文档，不写旧库或全仓同步基线；完整扫描须明确请求 full-update。",
    ]
    quote = lambda p: json.dumps(str(p), ensure_ascii=False)
    if regular_file(scope, "docs/PROJECT_GUIDE.md"):
        lines.append("当前 scope 导航：" + quote(scope / "docs/PROJECT_GUIDE.md"))
    elif scope != main_scope and regular_file(main_scope, "docs/PROJECT_GUIDE.md"):
        lines.append("主工作树导航（父分支视角，不含本分支未合并改动）：" + quote(main_scope / "docs/PROJECT_GUIDE.md"))
    if regular_file(scope, "docs/knowledge/INDEX.md"):
        lines.append("旧库入口（读取前确认适用分支）：" + quote(scope / "docs/knowledge/INDEX.md"))
    elif scope != main_scope and regular_file(main_scope, "docs/knowledge/INDEX.md"):
        lines.append("主工作树旧库（父分支视角，不含本分支未合并改动）：" + quote(main_scope / "docs/knowledge/INDEX.md"))
    else:
        lines.append("未找到可安全引用的旧库入口；可继续查当前源码和权威文档，普通任务不自动 bootstrap。")
    output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}}
    if len(json.dumps(output, ensure_ascii=False).encode()) > MAX_OUTPUT_BYTES:
        output["hookSpecificOutput"]["additionalContext"] = "\n".join(lines[:3]) + "\n入口路径过长，未注入；按当前项目规则定位相关文档。"
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path)
    args = parser.parse_args()
    try:
        payload = {}
        if args.cwd is None and not sys.stdin.isatty():
            try:
                value = json.loads(sys.stdin.read(65536))
                if isinstance(value, dict):
                    payload = value
            except (ValueError, OSError):
                pass
        event_cwd = payload.get("cwd")
        fallback = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        cwd = args.cwd or Path(event_cwd if isinstance(event_cwd, str) and event_cwd else fallback)
        print(json.dumps(message(cwd), ensure_ascii=False))
    except (OSError, ValueError, subprocess.SubprocessError):
        # A navigation hook cannot block the user's actual task.
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "Codewise 导航暂不可用；未读取任何知识内容，请按当前项目规则定位资料。"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
