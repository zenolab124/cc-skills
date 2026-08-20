#!/usr/bin/env python3
"""Build a validated manifest for a Codewise branch knowledge directory.

Only the six entry categories are emitted. Control files are deliberately not
merge inputs, and every ``_deleted`` line is validated before any caller acts.
This command is read-only.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


CATEGORIES = (
    "domains",
    "shared",
    "decisions",
    "integrations",
    "workflows",
    "pitfalls",
)
CONTROL_NAMES = (
    "_meta.json",
    "_sync.json",
    "_deleted",
    "INDEX.md",
    ".lock",
    ".gitignore",
    "_identity.json",
    "_branches.json",
    "_dedup_pending.md",
    ".branches",
    ".archive",
)


class ValidationError(ValueError):
    pass


def contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def validate_entry_path(value: str) -> str:
    if not value or value != value.strip():
        raise ValidationError("path is empty or has surrounding whitespace")
    if contains_control_character(value):
        raise ValidationError("path contains a control character")
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValidationError("path must be a portable relative POSIX path")

    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValidationError("path escapes or does not name a canonical relative entry")
    if len(path.parts) < 2 or path.parts[0] not in CATEGORIES or path.suffix != ".md":
        raise ValidationError("path must be a .md file below one of the six entry categories")
    return path.as_posix()


def collect_entries(root: Path) -> list[str]:
    root_resolved = root.resolve()
    entries: list[str] = []
    for category in CATEGORIES:
        category_root = root / category
        if category_root.is_symlink():
            raise ValidationError(f"{category}/ is not a real directory")
        if not category_root.exists():
            continue
        if not category_root.is_dir():
            raise ValidationError(f"{category}/ is not a real directory")
        for path in category_root.rglob("*.md"):
            if path.is_symlink() or not path.is_file():
                raise ValidationError(f"entry is not a regular file: {path.relative_to(root)}")
            try:
                path.resolve().relative_to(root_resolved)
            except ValueError as exc:
                raise ValidationError(f"entry escapes branch root: {path.relative_to(root)}") from exc
            entries.append(validate_entry_path(path.relative_to(root).as_posix()))
    return sorted(set(entries))


def collect_deletions(root: Path) -> list[str]:
    path = root / "_deleted"
    if path.is_symlink():
        raise ValidationError("_deleted must be a regular file")
    if not path.exists():
        return []
    if not path.is_file():
        raise ValidationError("_deleted must be a regular file")

    deletions: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"cannot read _deleted as UTF-8: {exc}") from exc
    # splitlines() recognizes several control characters as separators. Check
    # the raw text first so an embedded form-feed/record-separator cannot be
    # erased into two apparently valid deletion paths. CRLF is accepted;
    # standalone CR and every other control remain invalid.
    normalized = text.replace("\r\n", "\n")
    if "\r" in normalized or any(
        ord(character) < 32 and character != "\n" for character in normalized
    ) or "\x7f" in normalized:
        raise ValidationError("_deleted contains a control character")
    lines = normalized.split("\n")
    for line_number, line in enumerate(lines, start=1):
        if not line:
            continue
        try:
            deletions.append(validate_entry_path(line))
        except ValidationError as exc:
            raise ValidationError(f"_deleted line {line_number}: {exc}") from exc
    return sorted(set(deletions))


def build_manifest(
    root_value: str | Path,
    kb_value: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(os.path.abspath(os.path.expanduser(str(root_value))))
    if root.is_symlink():
        raise ValidationError("branch root must not be a symlink")
    kb: Path | None = None
    if kb_value is not None:
        kb = Path(os.path.abspath(os.path.expanduser(str(kb_value))))
        if kb.is_symlink() or (kb / ".branches").is_symlink():
            raise ValidationError("KB and .branches must not be symlinks")
        try:
            relative = root.relative_to(kb / ".branches")
        except ValueError as exc:
            raise ValidationError("branch root must be below <KB>/.branches") from exc
        if len(relative.parts) != 1:
            raise ValidationError("branch root must be a direct child of <KB>/.branches")
        if root.resolve(strict=False).parent != (kb / ".branches").resolve(strict=False):
            raise ValidationError("branch root resolves outside <KB>/.branches")
    if not root.is_dir():
        raise ValidationError(f"branch root is not a directory: {root}")
    entries = collect_entries(root)
    deletions = collect_deletions(root)
    contradictory = sorted(set(entries) & set(deletions))
    if contradictory:
        raise ValidationError(
            "paths cannot be both present entries and deletions: " + ", ".join(contradictory)
        )
    return {
        "schema_version": 1,
        "branch_root": str(root),
        "kb": str(kb) if kb else None,
        "entries": entries,
        "deletions": deletions,
        "ignored_controls": [name for name in CONTROL_NAMES if (root / name).exists()],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("branch_root", help="Path to <KB>/.branches/<slug>")
    parser.add_argument(
        "--kb",
        required=True,
        help="Canonical <KB>; branch_root must be its direct .branches child",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()
    try:
        result = build_manifest(args.branch_root, args.kb)
    except ValidationError as exc:
        print(f"branch manifest invalid: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
