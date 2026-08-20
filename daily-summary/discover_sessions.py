#!/usr/bin/env python3
"""Discover Claude Code sessions active on a given date (Beijing time).

Usage:
    python3 discover_sessions.py [YYYY-MM-DD] [--min-messages N]

Output: JSON with sessions grouped by project.
"""

import json
import os
import glob
import sys
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))
DEFAULT_MIN_MESSAGES = 2
DAY_START_HOUR = 7  # 一天从 07:00 开始，到次日 06:59 结束


def make_day_window(target_date):
    """Return (start, end) datetimes for target_date's work day: 05:00 ~ next day 05:00."""
    d = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=BJT)
    start = d.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def discover_sessions(target_date, min_messages=DEFAULT_MIN_MESSAGES):
    base = os.path.expanduser("~/.claude/projects")
    day_start, day_end = make_day_window(target_date)
    raw = []

    for proj_dir in sorted(os.listdir(base)):
        proj_path = os.path.join(base, proj_dir)
        if not os.path.isdir(proj_path):
            continue

        for f in glob.glob(os.path.join(proj_path, "*.jsonl")):
            has_target = False
            ai_title = None
            first_ts = None
            last_ts = None
            msg_count = 0

            for line in open(f):
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                ts_str = obj.get("timestamp", "")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(
                            ts_str.replace("Z", "+00:00")
                        ).astimezone(BJT)
                        if day_start <= ts < day_end:
                            has_target = True
                        if not first_ts:
                            first_ts = ts
                        last_ts = ts
                    except Exception:
                        pass

                if obj.get("type") == "ai-title":
                    ai_title = obj.get("aiTitle", "")
                if obj.get("type") in ("user", "assistant"):
                    msg_count += 1

            if has_target and msg_count >= min_messages:
                proj_name = proj_dir.replace("-Users-xt-workspace-", "").replace(
                    "-Users-xt-", "~/"
                )
                raw.append(
                    {
                        "project": proj_name,
                        "session_file": f,
                        "ai_title": ai_title or "(untitled)",
                        "msg_count": msg_count,
                        "first_ts": first_ts.isoformat() if first_ts else "",
                        "last_ts": last_ts.isoformat() if last_ts else "",
                    }
                )

    raw.sort(key=lambda x: x["first_ts"])

    # Group by project
    groups = {}
    for s in raw:
        p = s["project"]
        if p not in groups:
            groups[p] = []
        groups[p].append(s)

    return groups


def main():
    target_date = None
    min_messages = DEFAULT_MIN_MESSAGES

    args = sys.argv[1:]
    for a in args:
        if a.startswith("--min-messages"):
            continue
        if a.isdigit() and len(a) < 5:
            min_messages = int(a)
        elif len(a) == 10 and a[4] == "-":
            target_date = a

    if "--min-messages" in sys.argv:
        idx = sys.argv.index("--min-messages")
        if idx + 1 < len(sys.argv):
            min_messages = int(sys.argv[idx + 1])

    if not target_date:
        target_date = datetime.now(BJT).strftime("%Y-%m-%d")

    groups = discover_sessions(target_date, min_messages)

    if not groups:
        print(f"No sessions found for {target_date}", file=sys.stderr)
        sys.exit(0)

    # Output summary + full data
    total = sum(len(v) for v in groups.values())
    summary = {
        "target_date": target_date,
        "min_messages": min_messages,
        "total_sessions": total,
        "projects": {},
    }
    for proj, sessions in groups.items():
        summary["projects"][proj] = {
            "session_count": len(sessions),
            "total_messages": sum(s["msg_count"] for s in sessions),
            "time_range": f'{sessions[0]["first_ts"][:16]} ~ {sessions[-1]["last_ts"][:16]}',
            "sessions": sessions,
        }

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
