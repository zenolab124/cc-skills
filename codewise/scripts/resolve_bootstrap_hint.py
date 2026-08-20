#!/usr/bin/env python3
"""Read and validate a tracked Codewise cross-machine bootstrap hint."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit


HINT_NAME = ".codewise-bootstrap.json"
REQUIRED_KEYS = {
    "schema_version",
    "knowledge_path",
    "knowledge_remote",
    "knowledge_branch",
    "source_branch",
}


class HintError(ValueError):
    pass


def lexical(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def canonical(value: str | Path) -> Path:
    return Path(os.path.expanduser(str(value))).resolve(strict=False)


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def validate_branch(root: Path, value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise HintError(f"{field} must be a non-empty string")
    result = git(root, "check-ref-format", "--branch", value)
    if result.returncode != 0:
        raise HintError(f"{field} is not a safe Git branch name")
    return value


def validate_remote(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise HintError("knowledge_remote must be a non-empty URL")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise HintError("knowledge_remote must not contain whitespace or control characters")

    if value.startswith("git@"):
        authority, separator, repository = value.partition(":")
        if not separator or authority == "git@" or not repository or repository.startswith("/"):
            raise HintError("knowledge_remote has an invalid SSH shorthand")
        return value

    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "ssh"} or not parsed.hostname:
        raise HintError("knowledge_remote must use https://, ssh://, or git@host:path")
    if parsed.username or parsed.password:
        raise HintError("knowledge_remote must not embed credentials")
    if not parsed.path or parsed.path == "/":
        raise HintError("knowledge_remote must identify a repository path")
    return value


def load_hint(root_value: str | Path, kb_value: str | Path) -> dict[str, Any]:
    root = lexical(root_value)
    kb = lexical(kb_value)
    hint = root / HINT_NAME

    if hint.is_symlink():
        raise HintError(f"{HINT_NAME} must not be a symlink")
    try:
        mode = hint.lstat().st_mode
    except FileNotFoundError as exc:
        raise HintError(f"{HINT_NAME} does not exist") from exc
    except OSError as exc:
        raise HintError(f"cannot inspect {HINT_NAME}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise HintError(f"{HINT_NAME} must be a regular file")

    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise HintError("source root is not inside a Git repository")
    repo_root = canonical(top.stdout.removesuffix("\n"))
    try:
        tracked_path = canonical(hint).relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise HintError(f"{HINT_NAME} is outside the source repository") from exc

    tracked = git(root, "ls-files", "--error-unmatch", "--", tracked_path)
    if tracked.returncode != 0:
        raise HintError(f"{HINT_NAME} must be tracked by the source repository")
    unchanged = git(root, "diff", "--quiet", "HEAD", "--", tracked_path)
    if unchanged.returncode != 0:
        raise HintError(f"{HINT_NAME} must match committed HEAD")

    try:
        value = json.loads(hint.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HintError(f"cannot parse {HINT_NAME}: {exc}") from exc
    if not isinstance(value, dict):
        raise HintError(f"{HINT_NAME} must contain a JSON object")
    unknown = set(value) - REQUIRED_KEYS
    missing = REQUIRED_KEYS - set(value)
    if unknown:
        raise HintError(f"unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise HintError(f"missing fields: {', '.join(sorted(missing))}")
    if value["schema_version"] != 1:
        raise HintError("schema_version must be 1")

    knowledge_path = value["knowledge_path"]
    if (
        not isinstance(knowledge_path, str)
        or not knowledge_path
        or knowledge_path.startswith("/")
        or any(part in {"", ".", ".."} for part in knowledge_path.split("/"))
    ):
        raise HintError("knowledge_path must be a safe relative path")
    hinted_kb = canonical(root / knowledge_path)
    if hinted_kb != canonical(kb):
        raise HintError("knowledge_path differs from the resolver-selected KB")

    return {
        "schema_version": 1,
        "hint": str(hint),
        "knowledge_path": knowledge_path,
        "knowledge_remote": validate_remote(value["knowledge_remote"]),
        "knowledge_branch": validate_branch(root, value["knowledge_branch"], "knowledge_branch"),
        "source_branch": validate_branch(root, value["source_branch"], "source_branch"),
        "tracked_at_head": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--kb", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        result = load_hint(args.root, args.kb)
    except HintError as exc:
        print(f"Codewise bootstrap hint invalid: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
