#!/usr/bin/env python3
"""Extract and compress a Claude Code session for daily summarization.

Strips tool_result and thinking blocks (which dominate file size),
keeps user text, assistant text, and tool_use names with key params.
Annotates date boundaries and marks target-date messages with '>>>'.
"""

import json
import sys
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))
DAY_START_HOUR = 7


def make_day_window(target_date):
    d = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=BJT)
    start = d.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def parse_ts(ts_str):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(BJT)
    except Exception:
        return None


def extract_user_text(content):
    if not isinstance(content, list):
        return ""
    parts = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            t = c.get("text", "").strip()
            if t:
                parts.append(t)
    return "\n".join(parts)


def extract_assistant_content(content):
    if not isinstance(content, list):
        return "", []
    texts = []
    tools = []
    for c in content:
        if not isinstance(c, dict):
            continue
        ct = c.get("type")
        if ct == "text":
            t = c.get("text", "").strip()
            if t:
                texts.append(t)
        elif ct == "tool_use":
            name = c.get("name", "?")
            inp = c.get("input", {})
            hint = ""
            if name == "Read" and inp.get("file_path"):
                hint = inp["file_path"].rsplit("/", 1)[-1]
            elif name == "Bash" and inp.get("command"):
                hint = inp["command"][:80]
            elif name in ("Edit", "Write") and inp.get("file_path"):
                hint = inp["file_path"].rsplit("/", 1)[-1]
            elif name in ("WebSearch",) and inp.get("query"):
                hint = inp["query"][:60]
            elif name == "WebFetch" and inp.get("url"):
                hint = inp["url"][:80]
            elif name == "Agent" and inp.get("description"):
                hint = inp["description"][:60]
            tools.append(f"{name}: {hint}" if hint else name)
    return "\n".join(texts), tools


def format_msg(m):
    marker = ">>>" if m["on_target"] else "   "
    role = "USER" if m["role"] == "user" else "CLAUDE"
    header = f'{marker} [{m["time"]} {role}]'
    parts = []
    if m["text"]:
        parts.append(f"{header} {m['text']}")
    if m["tools"]:
        tool_str = " | ".join(m["tools"])
        if m["text"]:
            parts.append(f"    tools: {tool_str}")
        else:
            parts.append(f"{header} [tools: {tool_str}]")
    return "\n".join(parts)


def main():
    if len(sys.argv) < 3:
        print("Usage: extract_session.py <jsonl_path> <target_date>", file=sys.stderr)
        sys.exit(1)

    jsonl_path = sys.argv[1]
    target_date = sys.argv[2]
    day_start, day_end = make_day_window(target_date)

    messages = []
    ai_title = None

    with open(jsonl_path) as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue

            t = obj.get("type")
            ts = parse_ts(obj.get("timestamp", ""))

            if t == "ai-title":
                ai_title = obj.get("aiTitle", "")
            elif t == "user":
                text = extract_user_text(obj.get("message", {}).get("content", []))
                if text:
                    on_target = (day_start <= ts < day_end) if ts else False
                    messages.append(
                        {
                            "role": "user",
                            "time": ts.strftime("%H:%M") if ts else "??:??",
                            "date": ts.strftime("%Y-%m-%d") if ts else "",
                            "text": text,
                            "tools": [],
                            "on_target": on_target,
                        }
                    )
            elif t == "assistant":
                content = obj.get("message", {}).get("content", [])
                text, tools = extract_assistant_content(content)
                if text or tools:
                    on_target = (day_start <= ts < day_end) if ts else False
                    messages.append(
                        {
                            "role": "assistant",
                            "time": ts.strftime("%H:%M") if ts else "??:??",
                            "date": ts.strftime("%Y-%m-%d") if ts else "",
                            "text": text,
                            "tools": tools,
                            "on_target": on_target,
                        }
                    )

    if not messages:
        sys.exit(0)

    lines = []
    if ai_title:
        lines.append(f"Session title: {ai_title}")
    lines.append(f"Target date: {target_date}")
    lines.append("")

    prev_date = None
    for m in messages:
        if m["date"] and m["date"] != prev_date:
            lines.append(f"\n--- {m['date']} ---")
            prev_date = m["date"]
        lines.append(format_msg(m))

    print("\n".join(lines))


if __name__ == "__main__":
    main()
