#!/usr/bin/env python3
"""Validate that writable Codewise KB and registry paths cannot escape via symlinks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


CATEGORIES = ("domains", "shared", "decisions", "integrations", "workflows", "pitfalls")
CONTROL_FILES = (
    "_identity.json",
    "_meta.json",
    "_branches.json",
    "_sync.json",
    "INDEX.md",
    "_deleted",
    "_dedup_pending.md",
    ".gitignore",
)


class GuardError(ValueError):
    pass


def lexical(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def check_not_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise GuardError(f"{label} must not be a symlink: {path}")


def check_optional_regular(path: Path, label: str) -> None:
    check_not_symlink(path, label)
    if not os.path.lexists(path):
        return
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise GuardError(f"cannot inspect {label}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise GuardError(f"{label} must be a regular file: {path}")


def guard(kb_value: str | Path, scope_value: str | Path) -> dict[str, Any]:
    kb = lexical(kb_value)
    scope = lexical(scope_value)
    check_not_symlink(kb, "KB")
    if not kb.is_dir():
        raise GuardError(f"KB is not a directory: {kb}")
    git_dir = kb / ".git"
    check_not_symlink(git_dir, "KB .git")
    if not git_dir.is_dir():
        raise GuardError("KB .git must be a real directory owned by this repository")

    checked = 0
    for name in CONTROL_FILES:
        check_optional_regular(kb / name, name)
        checked += 1
    for name in (".branches", ".archive", *CATEGORIES):
        path = kb / name
        check_not_symlink(path, name)
        if path.exists() and not path.is_dir():
            raise GuardError(f"expected directory: {path}")
        checked += 1

    for base_name in (*CATEGORIES, ".branches", ".archive"):
        base = kb / base_name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(kb)
            check_not_symlink(path, str(relative))
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise GuardError(f"path must be a regular file or directory: {relative}")
            if path.resolve(strict=False).is_relative_to(kb.resolve(strict=False)) is False:
                raise GuardError(f"path resolves outside KB: {relative}")
            checked += 1

    for name in ("AGENTS.md", "CLAUDE.md"):
        path = scope / name
        check_optional_regular(path, f"registry {name}")
        checked += 1

    return {
        "schema_version": 1,
        "kb": str(kb),
        "scope": str(scope),
        "checked_paths": checked,
        "hard_stops": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kb")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        result = guard(args.kb, args.scope)
    except GuardError as exc:
        print(f"KB tree invalid: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
