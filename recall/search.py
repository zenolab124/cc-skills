#!/usr/bin/env python3
"""Search past Claude Code sessions for a keyword.

Reads ~/.claude/projects/**/*.jsonl, matches a regex across user/assistant
text and tool-use summaries, and prints matched windows with ±N context.
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


def flatten_content(msg: dict) -> list[str]:
    """Return a list of short lines describing this message's content.

    Each line is prefixed by role/tool so the reader knows its nature.
    Tool outputs are omitted; tool calls are summarized.
    """
    role = msg.get("type")  # "user" | "assistant" | others
    content = msg.get("message", {}).get("content")
    lines: list[str] = []

    if isinstance(content, str):
        if content.strip():
            lines.append(f"[{role}] {content.strip()}")
        return lines

    if not isinstance(content, list):
        return lines

    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            text = (part.get("text") or "").strip()
            if text:
                lines.append(f"[{role}] {text}")
        elif ptype == "tool_use":
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


def parse_session(path: Path) -> tuple[str | None, list[tuple[str, list[str]]]]:
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
                lines = flatten_content(msg)
                if lines:
                    entries.append((msg.get("timestamp", ""), lines))
    except OSError:
        pass
    return cwd, entries


def format_session_header(path: Path, cwd: str | None, first_ts: str) -> str:
    session_id = path.stem[:8]
    when = first_ts[:16].replace("T", " ") if first_ts else "?"
    project = Path(cwd).name if cwd else "?"
    return f"── session {session_id} · {when} · {project} ──"


def main() -> int:
    ap = argparse.ArgumentParser(description="Recall past Claude Code sessions")
    ap.add_argument("pattern", help="regex, case-insensitive")
    ap.add_argument("--project", help="only sessions whose cwd contains this substring")
    ap.add_argument("--current", action="store_true", help="shortcut for --project=$PWD")
    ap.add_argument("--days", type=int, help="only sessions modified within N days")
    ap.add_argument("--context", type=int, default=3, help="± messages of context (default 3)")
    ap.add_argument("--limit", type=int, default=20, help="max windows to print (default 20)")
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

    files = sorted(
        PROJECTS.glob("*/*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    printed = 0
    total_hits = 0
    for path in files:
        if cutoff:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue

        cwd, entries = parse_session(path)
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

        header = format_session_header(path, cwd, entries[0][0])
        print(header)
        if cwd:
            print(f"  cwd: {cwd}")
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
