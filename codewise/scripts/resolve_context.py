#!/usr/bin/env python3
"""Resolve a Codewise scope to its one canonical knowledge-base path.

This command is read-only.  It centralizes worktree, scope, ignore, parent-index,
and identity checks so callers never reconstruct ``<KB>`` from their cwd.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def canonical(value: str | Path) -> Path:
    return Path(os.path.expanduser(str(value))).resolve(strict=False)


def lexical_absolute(value: str | Path) -> Path:
    """Normalize an absolute path without following its final symlink."""
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def symlink_components(path: Path, parent: Path) -> list[str]:
    """List symlinks from ``parent`` down to ``path`` (including dangling)."""
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return []
    found: list[str] = []
    current = parent
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            found.append(str(current))
    return found


def git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def source_status(root: Path) -> tuple[list[str], str | None]:
    result = git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", ".")
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git status exited {result.returncode}"
        return [], detail
    return [line for line in result.stdout.splitlines() if line], None


def git_text(root: Path, *args: str) -> str | None:
    result = git(root, *args)
    return result.stdout.strip() if result.returncode == 0 else None


def git_path_text(root: Path, *args: str) -> str | None:
    """Read a Git path record without destroying legal leading/trailing spaces."""
    result = git(root, *args)
    if result.returncode != 0:
        return None
    return result.stdout.removesuffix("\n")


def has_git_marker(root: Path) -> bool:
    current = root
    while True:
        if os.path.lexists(current / ".git"):
            return True
        if current == current.parent:
            return False
        current = current.parent


def independent_repository(kb: Path) -> tuple[bool, str | None]:
    git_dir = kb / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        return False, "KB .git must be a real directory, not a gitfile or symlink"
    top_result = git(kb, "rev-parse", "--show-toplevel")
    common_result = git(kb, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if top_result.returncode != 0 or common_result.returncode != 0:
        return False, "KB Git metadata is unavailable"
    top = canonical(top_result.stdout.removesuffix("\n"))
    common = canonical(common_result.stdout.removesuffix("\n"))
    expected_common = canonical(git_dir)
    if top != kb or common != expected_common:
        return False, "KB must own its Git common directory"
    return True, None


def parse_worktrees(text: str) -> list[dict[str, Any]]:
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*text.splitlines(), ""]:
        if not line:
            if current.get("root"):
                worktrees.append(current)
            current = {}
            continue
        key, separator, value = line.partition(" ")
        if key == "worktree" and separator:
            current["root"] = str(canonical(value))
        elif key == "branch" and separator:
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "HEAD" and separator:
            current["head"] = value
        elif key in ("bare", "detached"):
            current[key] = True
    return worktrees


def repository_history(root: Path) -> tuple[str | None, list[str], bool, str | None]:
    """Return HEAD, roots, shallow flag, and a fail-closed diagnostic."""
    shallow_result = git(root, "rev-parse", "--is-shallow-repository")
    if shallow_result.returncode != 0:
        detail = shallow_result.stderr.strip() or "cannot determine shallow state"
        return None, [], False, detail
    shallow = shallow_result.stdout.strip() == "true"
    if shallow:
        return None, [], True, "shallow repository cannot provide stable root commits"

    head_result = git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head_result.returncode != 0:
        count_result = git(root, "rev-list", "--all", "--count")
        if count_result.returncode == 0 and count_result.stdout.strip() == "0":
            return None, [], False, None
        detail = head_result.stderr.strip() or "HEAD commit is unavailable"
        return None, [], False, detail

    head = head_result.stdout.strip()
    roots_result = git(root, "rev-list", "--max-parents=0", head)
    roots = sorted(line for line in roots_result.stdout.splitlines() if line)
    if roots_result.returncode != 0 or not roots:
        detail = roots_result.stderr.strip() or "root commits are unavailable"
        return head, [], False, detail
    return head, roots, False, None


def load_identity(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if path.is_symlink():
        return None, "identity must not be a symlink"
    if not path.is_file():
        return None, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "identity must be a JSON object"
    scope_root = value.get("scope_root")
    if (
        not isinstance(scope_root, str)
        or not scope_root
        or "\x00" in scope_root
        or scope_root.startswith("/")
        or any(part in ("", "..") for part in scope_root.split("/"))
    ):
        return None, "identity scope_root must be '.' or a safe relative path"
    roots = value.get("root_commits")
    weak = value.get("weak_path_sha256")
    if (roots is None) == (weak is None):
        return None, "identity must contain exactly one strong or weak anchor"
    if roots is not None:
        if (
            not isinstance(roots, list)
            or not roots
            or any(
                not isinstance(item, str)
                or len(item) not in (40, 64)
                or any(char not in "0123456789abcdefABCDEF" for char in item)
                for item in roots
            )
            or len(set(roots)) != len(roots)
        ):
            return None, "identity root_commits must be unique full hexadecimal object ids"
    if weak is not None and (
        not isinstance(weak, str)
        or len(weak) != 16
        or any(char not in "0123456789abcdefABCDEF" for char in weak)
    ):
        return None, "identity weak_path_sha256 must be 16 hexadecimal characters"
    return value, None


def resolve(root_value: str | Path, allow_reidentify: bool = False) -> dict[str, Any]:
    root = canonical(root_value)
    if not root.is_dir():
        raise ValueError(f"scope root is not an existing directory: {root}")

    repo_result = git(root, "rev-parse", "--show-toplevel")
    if repo_result.returncode != 0:
        if has_git_marker(root):
            return {
                "schema_version": 1,
                "root": str(root),
                "is_git": None,
                "mode": "blocked",
                "hard_stops": ["source-repository-unavailable"],
                "source_repository_error": repo_result.stderr.strip()
                or "git rev-parse failed",
            }
        kb = lexical_absolute(root / "docs" / "knowledge")
        kb_symlinks = symlink_components(kb, root)
        kb_resolves_outside = not is_within(canonical(kb), root)
        kb_exists = os.path.lexists(kb)
        kb_independent, kb_repo_error = (
            independent_repository(kb)
            if kb_exists and not kb_symlinks and kb.is_dir()
            else (False, None)
        )
        expected_identity = {
            "weak_path_sha256": hashlib.sha256(str(root).encode()).hexdigest()[:16],
            "scope_root": ".",
        }
        identity, identity_error = (
            load_identity(kb / "_identity.json")
            if kb_exists and not kb_symlinks and kb.is_dir()
            else (None, None)
        )
        identity_matches: bool | None = None
        if identity is not None:
            identity_matches = bool(
                identity.get("weak_path_sha256") == expected_identity["weak_path_sha256"]
                and identity.get("scope_root") == "."
            )
        hard_stops: list[str] = []
        if kb_symlinks:
            hard_stops.append("kb-path-is-symlink")
        if kb_resolves_outside:
            hard_stops.append("kb-resolves-outside-scope")
        if kb_exists and not kb_symlinks and not kb_independent:
            hard_stops.append("kb-not-independent-repository")
        if identity_error:
            hard_stops.append(
                "identity-path-is-symlink"
                if "symlink" in identity_error
                else "identity-invalid"
            )
        elif kb_independent and identity is None:
            hard_stops.append("identity-missing")
        elif identity_matches is False:
            hard_stops.append("identity-mismatch")
        identity_override = []
        if allow_reidentify and kb_independent:
            overridable = {"identity-missing", "identity-mismatch", "identity-invalid"}
            identity_override = [item for item in hard_stops if item in overridable]
            hard_stops = [item for item in hard_stops if item not in overridable]
        return {
            "schema_version": 1,
            "root": str(root),
            "is_git": False,
            "has_commits": False,
            "scope_root": ".",
            "kb": str(kb),
            "kb_path_symlinks": kb_symlinks,
            "kb_resolves_outside_scope": kb_resolves_outside,
            "kb_exists": kb_exists,
            "kb_is_independent_repository": kb_independent,
            "kb_repository_error": kb_repo_error,
            "expected_identity": expected_identity,
            "identity": identity,
            "identity_error": identity_error,
            "identity_matches": identity_matches,
            "identity_override_requested": allow_reidentify,
            "identity_override_reasons": identity_override,
            "mode": (
                "blocked"
                if hard_stops
                else "existing-non-git"
                if kb_exists
                else "initialize-non-git"
            ),
            "hard_stops": hard_stops,
            "source_dirty": False,
            "source_changes": [],
            "source_head": None,
        }

    repo_path_text = repo_result.stdout.removesuffix("\n")
    repo_root = canonical(repo_path_text)
    prefix_result = git(root, "rev-parse", "--show-prefix")
    worktree_result = git(root, "worktree", "list", "--porcelain")
    prefix_error = prefix_result.returncode != 0
    worktree_error = worktree_result.returncode != 0 or not worktree_result.stdout.strip()
    prefix_text = prefix_result.stdout.removesuffix("\n")
    scope_rel = prefix_text.removesuffix("/") or "." if not prefix_error else "."
    worktrees = parse_worktrees(worktree_result.stdout) if not worktree_error else []
    main_candidates = [canonical(item["root"]) for item in worktrees if not item.get("bare")]
    main = main_candidates[0] if main_candidates else repo_root
    current_common = git_path_text(
        root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    main_common = git_path_text(
        main, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    same_common_dir = bool(
        current_common
        and main_common
        and canonical(current_common) == canonical(main_common)
    )
    kb = lexical_absolute(main / ("" if scope_rel == "." else scope_rel) / "docs" / "knowledge")
    kb_symlinks = symlink_components(kb, main)
    kb_resolves_outside = not is_within(canonical(kb), main)
    kb_exists = os.path.lexists(kb)
    kb_independent, kb_repo_error = (
        independent_repository(kb)
        if kb_exists and not kb_symlinks and kb.is_dir()
        else (False, None)
    )

    kb_rel = "docs/knowledge" if scope_rel == "." else f"{scope_rel}/docs/knowledge"
    tracked_text = git_text(main, "ls-files", "--", kb_rel) or ""
    tracked = [line for line in tracked_text.splitlines() if line]
    ignored = git(main, "check-ignore", "-q", "--no-index", "--", kb_rel).returncode == 0
    source_head, commits, source_shallow, history_error = repository_history(root)
    source_changes, source_status_error = source_status(root)
    expected_identity = (
        {"root_commits": commits, "scope_root": scope_rel}
        if commits
        else {
            "weak_path_sha256": hashlib.sha256(str(root).encode()).hexdigest()[:16],
            "scope_root": scope_rel,
        }
    )
    identity, identity_error = (
        load_identity(kb / "_identity.json")
        if kb_exists and not kb_symlinks and kb.is_dir()
        else (None, None)
    )
    identity_matches: bool | None = None
    if identity is not None:
        if commits:
            identity_matches = bool(
                sorted(identity.get("root_commits") or []) == commits
                and identity.get("scope_root") == scope_rel
            )
        else:
            identity_matches = bool(
                identity.get("weak_path_sha256") == expected_identity["weak_path_sha256"]
                and identity.get("scope_root") == scope_rel
            )

    hard_stops: list[str] = []
    is_linked = repo_root != main
    if worktree_error or not main.is_dir() or not same_common_dir:
        hard_stops.append("main-worktree-resolution-failed")
    if prefix_error:
        hard_stops.append("scope-resolution-failed")
    current_expected_root = repo_root if scope_rel == "." else repo_root / scope_rel
    if canonical(current_expected_root) != root:
        hard_stops.append("scope-path-mismatch")
    if not is_within(kb, main):
        hard_stops.append("kb-path-outside-main-worktree")
    if kb_symlinks:
        hard_stops.append("kb-path-is-symlink")
    if kb_resolves_outside:
        hard_stops.append("kb-resolves-outside-main-worktree")
    if is_linked and not kb_exists:
        hard_stops.append("linked-worktree-kb-missing")
    if kb_exists and not kb_symlinks and not kb_independent:
        hard_stops.append("kb-not-independent-repository")
    if kb_exists and not ignored:
        hard_stops.append("kb-not-ignored-by-main-repository")
    if tracked:
        hard_stops.append("kb-tracked-by-main-repository")
    if source_shallow:
        hard_stops.append("source-repository-shallow")
    elif history_error:
        hard_stops.append("source-history-unavailable")
    if source_status_error:
        hard_stops.append("source-status-unavailable")
    if identity_error:
        hard_stops.append(
            "identity-path-is-symlink"
            if "symlink" in identity_error
            else "identity-invalid"
        )
    elif kb_independent and identity is None:
        hard_stops.append("identity-missing")
    elif identity_matches is False:
        hard_stops.append("identity-mismatch")
    identity_override = []
    if allow_reidentify and kb_independent:
        overridable = {"identity-missing", "identity-mismatch", "identity-invalid"}
        identity_override = [item for item in hard_stops if item in overridable]
        hard_stops = [item for item in hard_stops if item not in overridable]

    return {
        "schema_version": 1,
        "root": str(root),
        "is_git": True,
        "has_commits": bool(commits),
        "repo_root": str(repo_root),
        "main_worktree": str(main),
        "git_common_dir_matches": same_common_dir,
        "scope_root": scope_rel,
        "kb": str(kb),
        "kb_path_symlinks": kb_symlinks,
        "kb_resolves_outside_main_worktree": kb_resolves_outside,
        "kb_relative_to_main": kb_rel,
        "is_linked_worktree": is_linked,
        "kb_exists": kb_exists,
        "kb_is_independent_repository": kb_independent,
        "kb_repository_error": kb_repo_error,
        "ignored_by_main_repository": ignored,
        "tracked_by_main_repository": tracked,
        "expected_identity": expected_identity,
        "identity": identity,
        "identity_error": identity_error,
        "identity_matches": identity_matches,
        "identity_override_requested": allow_reidentify,
        "identity_override_reasons": identity_override,
        "source_head": source_head,
        "source_repository_shallow": source_shallow,
        "source_history_error": history_error,
        "source_status_error": source_status_error,
        "source_dirty": bool(source_changes),
        "source_changes": source_changes,
        "mode": (
            "blocked"
            if hard_stops
            else "existing"
            if kb_exists
            else "initialize-main"
        ),
        "hard_stops": hard_stops,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="Resolved Codewise scope root")
    parser.add_argument(
        "--reidentify",
        action="store_true",
        help="Report identity-only failures as an explicit override request; callers must confirm",
    )
    parser.add_argument(
        "--verify-source-head",
        metavar="COMMIT",
        help="Hard-stop if the current source HEAD differs from the captured snapshot",
    )
    parser.add_argument(
        "--require-clean-source",
        action="store_true",
        help="Hard-stop if the requested source scope has tracked or untracked changes",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()
    try:
        result = resolve(args.root, allow_reidentify=args.reidentify)
    except ValueError as exc:
        parser.error(str(exc))
    if args.verify_source_head and result.get("source_head") != args.verify_source_head:
        result.setdefault("hard_stops", []).append("source-head-changed")
    if args.require_clean_source and result.get("source_dirty"):
        result.setdefault("hard_stops", []).append("source-scope-dirty")
    if result.get("hard_stops"):
        result["mode"] = "blocked"
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 2 if result.get("hard_stops") else 0


if __name__ == "__main__":
    raise SystemExit(main())
