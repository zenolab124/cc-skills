#!/usr/bin/env python3
"""Capture and verify a stable source snapshot for Codewise writes.

The snapshot hashes project-owned source files while excluding the canonical
knowledge base, VCS metadata, dependencies, caches, and build outputs. It is a
fallback for non-Git or unborn-HEAD projects and a second guard for all modes.
This command is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Any


EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".cache",
    ".next",
    ".nuxt",
    ".turbo",
    ".vercel",
    ".netlify",
    ".venv",
    ".tox",
    ".gradle",
    ".build",
    "node_modules",
    "vendor",
    "target",
    "dist",
    "build",
    "out",
    "coverage",
    "tmp",
    "unpackage",
    "DerivedData",
    "Pods",
    "__pycache__",
}
EXCLUDED_FILES = {".DS_Store"}


class SnapshotError(ValueError):
    pass


def lexical_absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_file_list(root: Path) -> list[Path] | None:
    if git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return None
    result = git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        ".",
    )
    if result.returncode != 0:
        raise SnapshotError("git ls-files failed; cannot establish source snapshot")
    return [root / os.fsdecode(value) for value in result.stdout.split(b"\x00") if value]


def source_state(root: Path) -> dict[str, Any]:
    inside = git(root, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        return {"kind": "non-git", "has_commits": False, "head": None}
    head = git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head.returncode == 0:
        value = head.stdout.decode("ascii", errors="strict").strip()
        return {"kind": "git", "has_commits": True, "head": value}
    count = git(root, "rev-list", "--all", "--count")
    if count.returncode == 0 and count.stdout.strip() == b"0":
        return {"kind": "git", "has_commits": False, "head": None}
    raise SnapshotError("Git history state is unavailable")


def add_path(
    digest: Any,
    files: list[str] | None,
    root: Path,
    path: Path,
) -> tuple[bool, int]:
    relative = path.relative_to(root).as_posix()
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            data = os.readlink(path).encode("utf-8", errors="surrogateescape")
            kind = b"symlink\0"
        elif stat.S_ISREG(metadata.st_mode):
            data = None
            kind = b"file\0"
        else:
            return False, 0
    except OSError as exc:
        raise SnapshotError(f"cannot read {relative}: {exc}") from exc
    encoded = relative.encode("utf-8")
    mode = metadata.st_mode & 0o777
    digest.update(kind)
    digest.update(mode.to_bytes(4, "big"))
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    data_size = len(data) if data is not None else metadata.st_size
    digest.update(data_size.to_bytes(8, "big"))
    if data is not None:
        digest.update(data)
    else:
        try:
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise SnapshotError(f"cannot read {relative}: {exc}") from exc
    if files is not None:
        files.append(relative)
    return True, data_size


def snapshot(
    root_value: str | Path,
    kb_value: str | Path,
    *,
    include_files: bool = False,
) -> dict[str, Any]:
    root = lexical_absolute(root_value)
    kb = lexical_absolute(kb_value)
    if root.is_symlink() or not root.is_dir():
        raise SnapshotError("root must be a real directory")

    digest = hashlib.sha256()
    state = source_state(root)
    state_bytes = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    digest.update(b"source-state\0")
    digest.update(state_bytes)
    files: list[str] | None = [] if include_files else None
    file_count = 0
    byte_count = 0
    git_paths = git_file_list(root)
    if git_paths is not None:
        candidates = sorted(git_paths, key=lambda path: path.relative_to(root).as_posix())
        for path in candidates:
            lexical = lexical_absolute(path)
            if is_within(lexical, kb) or path.name in EXCLUDED_FILES:
                continue
            if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts[:-1]):
                continue
            included, size = add_path(digest, files, root, path)
            file_count += int(included)
            byte_count += size
    else:
        stack = [root]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(directory.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                raise SnapshotError(f"cannot enumerate {directory}: {exc}") from exc
            for path in entries:
                lexical = lexical_absolute(path)
                if is_within(lexical, kb) or path.name in EXCLUDED_FILES:
                    continue
                if path.is_symlink():
                    included, size = add_path(digest, files, root, path)
                    file_count += int(included)
                    byte_count += size
                    continue
                if path.is_dir():
                    if path.name not in EXCLUDED_DIRS:
                        stack.append(path)
                    continue
                included, size = add_path(digest, files, root, path)
                file_count += int(included)
                byte_count += size
    result = {
        "schema_version": 1,
        "root": str(root),
        "kb": str(kb),
        "digest": digest.hexdigest(),
        "file_count": file_count,
        "byte_count": byte_count,
        "source_state": state,
    }
    if files is not None:
        result["files"] = files
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--kb", required=True, help="Canonical KB path to exclude")
    parser.add_argument("--verify", metavar="SHA256", help="Exit 2 if digest differs")
    parser.add_argument("--list-files", action="store_true", help="Include every path in JSON")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        result = snapshot(args.root, args.kb, include_files=args.list_files)
    except SnapshotError as exc:
        print(f"source snapshot invalid: {exc}", file=sys.stderr)
        return 2
    if args.verify and result["digest"] != args.verify:
        result["hard_stops"] = ["source-snapshot-changed"]
    else:
        result["hard_stops"] = []
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 2 if result["hard_stops"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
