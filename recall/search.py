#!/usr/bin/env python3
"""Search past Claude Code and Codex sessions for a keyword.

Reads ~/.claude/projects/**/*.jsonl and ~/.codex/{sessions,archived_sessions}
rollouts, matches a regex across the user/assistant conversation (tool calls
only with --tools), and prints matched windows with ±N context.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

PROJECTS = Path.home() / ".claude" / "projects"
CODEX_DIRS = [Path.home() / ".codex" / "sessions", Path.home() / ".codex" / "archived_sessions"]

# Harness-injected blocks inside user turns; whitelisted so pasted markup survives.
INJECTED_TAGS = (
    "system-reminder", "task-notification", "ide_opened_file", "ide_selection",
    "command-name", "command-message", "command-args", "local-command-stdout",
    "local-command-stderr", "local-command-caveat", "in-app-browser-context",
    "environment_context", "user_instructions",
)
INJECTED_RE = re.compile(
    r"<(%s)\b[^>]*>.*?</\1>" % "|".join(map(re.escape, INJECTED_TAGS)), re.DOTALL
)
# Claude user entries that are not typed by the user (compaction summary, skill body, image hints)
CLAUDE_META_FLAGS = ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly")


def clean_text(role: str, text: str) -> str:
    if role == "user":
        text = INJECTED_RE.sub("", text)
        if text.strip().startswith("[Request interrupted by user"):
            return ""
    return text.strip()


def flatten_content(msg: dict, with_tools: bool) -> list[str]:
    """Return a list of short lines describing this message's content.

    Each line is prefixed by role/tool so the reader knows its nature.
    Tool outputs, thinking and images are omitted; tool calls only with_tools.
    """
    role = msg.get("type")  # "user" | "assistant" | others
    if role not in ("user", "assistant") or any(msg.get(k) for k in CLAUDE_META_FLAGS):
        return []
    content = msg.get("message", {}).get("content")
    lines: list[str] = []

    if isinstance(content, str):
        text = clean_text(role, content)
        if text:
            lines.append(f"[{role}] {text}")
        return lines

    if not isinstance(content, list):
        return lines

    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            text = clean_text(role, part.get("text") or "")
            if text:
                lines.append(f"[{role}] {text}")
        elif ptype == "tool_use" and with_tools:
            name = part.get("name", "?")
            inp = part.get("input", {}) or {}
            summary = summarize_tool(name, inp)
            lines.append(f"[tool:{name}] {summary}")
        elif ptype == "tool_result":
            # skip outputs; too noisy
            pass
        elif ptype == "thinking":
            pass
    return lines


def summarize_tool(name: str, inp: dict) -> str:
    """Compact one-line summary of a tool call's key argument."""
    key_fields = {
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Glob": "pattern",
        "Grep": "pattern",
        "Bash": "command",
        "WebFetch": "url",
        "WebSearch": "query",
        "Skill": "skill",
        "Agent": "description",
    }
    key = key_fields.get(name)
    if key and key in inp:
        val = str(inp[key]).replace("\n", " ")
        return val[:200]
    # fallback: first string-ish value
    for v in inp.values():
        if isinstance(v, str):
            return v.replace("\n", " ")[:200]
    return ""


def parse_session(path: Path, with_tools: bool) -> tuple[str | None, list[tuple[str, list[str]]]]:
    """Return (cwd, [(timestamp, lines)]) for a session file."""
    cwd = None
    entries: list[tuple[str, list[str]]] = []
    try:
        with path.open() as f:
            for raw in f:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if cwd is None:
                    cwd = msg.get("cwd")
                lines = flatten_content(msg, with_tools)
                if lines:
                    entries.append((msg.get("timestamp", ""), lines))
    except OSError:
        pass
    return cwd, entries


def codex_meta(path: Path) -> tuple[str | None, str]:
    """Read only the first line (session_meta) → (cwd, session id)."""
    try:
        with path.open() as f:
            first = json.loads(f.readline())
    except (OSError, json.JSONDecodeError):
        return None, path.stem
    payload = first.get("payload", {}) if first.get("type") == "session_meta" else {}
    return payload.get("cwd"), payload.get("id") or path.stem


def codex_item_lines(item: dict, with_tools: bool) -> list[str]:
    """Summarize one item_completed item (the clean high-level stream)."""
    itype = item.get("type")
    if itype in ("UserMessage", "AgentMessage"):
        role = "user" if itype == "UserMessage" else "assistant"
        texts = [
            clean_text(role, c.get("text") or "")
            for c in item.get("content") or []
            if isinstance(c, dict)
        ]
        return [f"[{role}] {t}" for t in texts if t]
    if not with_tools:
        return []
    if itype == "CommandExecution":
        cmd = item.get("command")
        cmd = cmd[-1] if isinstance(cmd, list) and cmd else str(cmd or "")
        return [f"[tool:exec] {cmd.replace(chr(10), ' ')[:200]}"]
    if itype == "FileChange":
        paths = ", ".join((item.get("changes") or {}).keys())
        return [f"[tool:edit] {paths[:200]}"]
    if itype == "McpToolCall":
        args = json.dumps(item.get("arguments") or {}, ensure_ascii=False)
        return [f"[tool:{item.get('server')}.{item.get('tool')}] {args[:200]}"]
    return []


def codex_legacy_lines(payload: dict, with_tools: bool) -> list[str]:
    """Fallback for rollouts without item_completed: raw response items."""
    ptype = payload.get("type")
    if ptype == "message" and payload.get("role") in ("user", "assistant"):
        role = payload["role"]
        lines = []
        for c in payload.get("content") or []:
            text = clean_text(role, c.get("text") or "") if isinstance(c, dict) else ""
            # raw user items also carry injected context blocks outside the whitelist
            if text and not (role == "user" and text.startswith("<")):
                lines.append(f"[{role}] {text}")
        return lines
    if ptype in ("function_call", "custom_tool_call") and with_tools:
        args = payload.get("arguments") or payload.get("input") or ""
        return [f"[tool:{payload.get('name', '?')}] {str(args).replace(chr(10), ' ')[:200]}"]
    return []


def parse_codex_session(path: Path, with_tools: bool) -> list[tuple[str, list[str]]]:
    items: list[tuple[str, list[str]]] = []
    legacy: list[tuple[str, list[str]]] = []
    has_items = False
    try:
        with path.open() as f:
            for raw in f:
                # most lines are token counts, reasoning and tool outputs; skip before json parsing
                if '"item_completed"' not in raw and '"response_item"' not in raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                payload = msg.get("payload") or {}
                ts = msg.get("timestamp", "")
                if msg.get("type") == "event_msg" and payload.get("type") == "item_completed":
                    has_items = True
                    lines = codex_item_lines(payload.get("item") or {}, with_tools)
                    if lines:
                        items.append((ts, lines))
                elif msg.get("type") == "response_item" and not has_items:
                    lines = codex_legacy_lines(payload, with_tools)
                    if lines:
                        legacy.append((ts, lines))
    except OSError:
        pass
    return items if has_items else legacy


def format_session_header(source: str, session_id: str, cwd: str | None, first_ts: str) -> str:
    when = first_ts[:16].replace("T", " ") if first_ts else "?"
    project = Path(cwd).name if cwd else "?"
    return f"── {source} {session_id} · {when} · {project} ──"


def main() -> int:
    ap = argparse.ArgumentParser(description="Recall past Claude Code / Codex sessions")
    ap.add_argument("pattern", help="regex, case-insensitive")
    ap.add_argument("--project", help="only sessions whose cwd contains this substring")
    ap.add_argument("--current", action="store_true", help="shortcut for --project=$PWD")
    ap.add_argument("--days", type=int, help="only sessions modified within N days")
    ap.add_argument("--context", type=int, default=3, help="± messages of context (default 3)")
    ap.add_argument("--limit", type=int, default=20, help="max windows to print (default 20)")
    ap.add_argument(
        "--source",
        choices=("all", "claude", "codex"),
        default="all",
        help="which agent's sessions to search (default all)",
    )
    ap.add_argument(
        "--tools",
        action="store_true",
        help="also match tool-call summaries (commands, edited files); default is conversation only",
    )
    args = ap.parse_args()

    try:
        rx = re.compile(args.pattern, re.IGNORECASE)
    except re.error as e:
        print(f"bad regex: {e}", file=sys.stderr)
        return 2

    project_filter = args.project
    if args.current:
        project_filter = os.getcwd()

    cutoff = None
    if args.days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    files: list[tuple[str, Path]] = []
    if args.source in ("all", "claude"):
        files += [("claude", p) for p in PROJECTS.glob("*/*.jsonl")]
    if args.source in ("all", "codex"):
        files += [("codex", p) for d in CODEX_DIRS for p in d.rglob("rollout-*.jsonl")]
    files.sort(key=lambda sp: sp[1].stat().st_mtime, reverse=True)

    printed = 0
    total_hits = 0
    for source, path in files:
        if cutoff:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue

        if source == "codex":
            # rollouts are large; filter by cwd from the first line before full parse
            cwd, session_id = codex_meta(path)
            if project_filter and (not cwd or project_filter not in cwd):
                continue
            entries = parse_codex_session(path, args.tools)
        else:
            cwd, entries = parse_session(path, args.tools)
            session_id = path.stem[:8]
            if project_filter and (not cwd or project_filter not in cwd):
                continue
        if not entries:
            continue

        # Find matching entry indices
        hits: list[int] = []
        for i, (_, lines) in enumerate(entries):
            if any(rx.search(l) for l in lines):
                hits.append(i)
        if not hits:
            continue

        # Merge overlapping context windows
        windows: list[tuple[int, int]] = []
        for i in hits:
            lo = max(0, i - args.context)
            hi = min(len(entries) - 1, i + args.context)
            if windows and lo <= windows[-1][1] + 1:
                windows[-1] = (windows[-1][0], max(windows[-1][1], hi))
            else:
                windows.append((lo, hi))

        header = format_session_header(source, session_id, cwd, entries[0][0])
        print(header)
        if cwd:
            print(f"  cwd: {cwd}")
        print(f"  file: {path}")
        for lo, hi in windows:
            for idx in range(lo, hi + 1):
                ts, lines = entries[idx]
                marker = "▸" if idx in hits else " "
                for l in lines:
                    # highlight the matched line
                    display = l if len(l) < 400 else l[:400] + "…"
                    print(f"  {marker} {display}")
            print("  ┄")
        print()

        total_hits += len(hits)
        printed += 1
        if printed >= args.limit:
            print(f"… stopped after {args.limit} sessions (use --limit to see more)")
            break

    if printed == 0:
        print("no matches", file=sys.stderr)
        return 1
    print(f"# {total_hits} hit(s) across {printed} session(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
