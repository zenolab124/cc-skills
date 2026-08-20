#!/usr/bin/env python3
"""Validate Codewise control files before they influence paths or Git commands.

The command is read-only.  It fails closed on malformed JSON, symlinks,
out-of-bounds branch roots, invalid branch registrations, and commit IDs that
cannot be resolved to commit objects.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any


CODEWISE_VERSION = 3
MAX_CONTROL_BYTES = 4 * 1024 * 1024
HEX_OID = re.compile(r"(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})\Z")
SAFE_SLUG = re.compile(r"[A-Za-z0-9._-]+\Z")
SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

ROOT_CONTROL_NAMES = (
    "_identity.json",
    "_meta.json",
    "_branches.json",
    "_sync.json",
    ".lock",
    ".gitignore",
    "INDEX.md",
    "_dedup_pending.md",
    ".branches",
    ".archive",
)
ARCHIVE_CONTROL_NAMES = (
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
BRANCH_CONTROL_NAMES = (
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
SYNC_KEYS = {
    "codewise_version",
    "baseline_commit",
    "synced_at",
    "scope_root",
    "multi_codetree",
    "session_sources",
    "worktree_count",
    "known_worktrees",
}


class ValidationError(ValueError):
    """A control state cannot be consumed safely."""


def lexical_absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def require_plain_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    if (not allow_empty and not value) or contains_control(value):
        raise ValidationError(f"{field} is empty or contains a control character")
    return value


def require_exact_keys(
    value: dict[str, Any],
    field: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    keys = set(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing:
        raise ValidationError(f"{field} is missing keys: {', '.join(missing)}")
    if unknown:
        raise ValidationError(f"{field} has unknown keys: {', '.join(unknown)}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValidationError(f"JSON contains unsupported numeric constant: {value}")


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise ValidationError(f"{label} must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValidationError(f"cannot open {label}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValidationError(f"{label} must be a regular file")
        if metadata.st_size > MAX_CONTROL_BYTES:
            raise ValidationError(f"{label} exceeds {MAX_CONTROL_BYTES} bytes")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(MAX_CONTROL_BYTES + 1)
    except OSError as exc:
        raise ValidationError(f"cannot read {label}: {exc}") from exc
    finally:
        os.close(descriptor)
    if len(raw) > MAX_CONTROL_BYTES:
        raise ValidationError(f"{label} exceeds {MAX_CONTROL_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except ValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must contain a JSON object")
    return value


def reject_control_symlinks(root: Path, names: tuple[str, ...], label: str) -> None:
    for name in names:
        if (root / name).is_symlink():
            raise ValidationError(f"{label}/{name} must not be a symlink")


def validate_branch_name(value: Any, field: str) -> str:
    branch = require_plain_string(value, field)
    try:
        result = subprocess.run(
            ["git", "check-ref-format", "--branch", branch],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ValidationError(f"cannot run git check-ref-format: {exc}") from exc
    normalized = result.stdout.rstrip("\n")
    if result.returncode != 0 or normalized != branch:
        raise ValidationError(f"{field} is not an acceptable Git branch name")
    return branch


def derive_slug(branch: str) -> str:
    return SLUG_UNSAFE.sub("-", branch)


def validate_slug(value: Any, branch: str, field: str) -> str:
    slug = require_plain_string(value, field)
    if (
        not SAFE_SLUG.fullmatch(slug)
        or slug in {".", ".."}
        or "/" in slug
        or "\\" in slug
        or len(slug.encode("utf-8")) > 255
    ):
        raise ValidationError(f"{field} is not a safe single path component")
    expected = derive_slug(branch)
    if slug != expected:
        raise ValidationError(f"{field} must be the canonical slug {expected!r}")
    return slug


def validate_archive_path(value: Any, field: str) -> str:
    archive_path = require_plain_string(value, field)
    if "\\" in archive_path or archive_path.startswith("/"):
        raise ValidationError(f"{field} must be a portable relative POSIX path")
    path = PurePosixPath(archive_path)
    if len(path.parts) != 2 or path.parts[0] != ".archive":
        raise ValidationError(f"{field} must be .archive/<safe-unique-name>")
    name = path.parts[1]
    if (
        not SAFE_SLUG.fullmatch(name)
        or name in {".", ".."}
        or len(name.encode("utf-8")) > 255
        or path.as_posix() != archive_path
    ):
        raise ValidationError(f"{field} has an unsafe archive directory name")
    return archive_path


def validate_archive_record(value: Any, branch: str, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object")
    require_exact_keys(
        value,
        field,
        {"archive_path", "archived_at", "outcome"},
        {"slug"},
    )
    record: dict[str, Any] = {
        "archive_path": validate_archive_path(value["archive_path"], f"{field}.archive_path"),
        "archived_at": validate_timestamp(
            value["archived_at"], f"{field}.archived_at", nullable=False
        ),
    }
    outcome = value["outcome"]
    if outcome not in {"merged", "abandoned"}:
        raise ValidationError(f"{field}.outcome must be 'merged' or 'abandoned'")
    record["outcome"] = outcome
    if "slug" in value:
        record["slug"] = validate_slug(value["slug"], branch, f"{field}.slug")
    return record


def validate_archive_history(value: Any, branch: str, field: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be an array")
    return [
        validate_archive_record(item, branch, f"{field}[{index}]")
        for index, item in enumerate(value)
    ]


def validate_timestamp(value: Any, field: str, *, nullable: bool) -> str | None:
    if value is None and nullable:
        return None
    timestamp = require_plain_string(value, field)
    candidate = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValidationError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{field} must include a timezone")
    return timestamp


def validate_scope_root(value: Any, field: str) -> str:
    scope = require_plain_string(value, field)
    if scope == ".":
        return scope
    if "\\" in scope or scope.startswith("/"):
        raise ValidationError(f"{field} must be a portable relative POSIX path")
    path = PurePosixPath(scope)
    if any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != scope:
        raise ValidationError(f"{field} must be a canonical relative path")
    return scope


def validate_known_worktree(value: Any, field: str) -> str:
    path_value = require_plain_string(value, field)
    if "\\" in path_value or path_value.startswith("/"):
        raise ValidationError(f"{field} must be a portable relative POSIX path")
    path = PurePosixPath(path_value)
    if not path.parts or any(part in {"", "."} for part in path.parts):
        if path_value != ".":
            raise ValidationError(f"{field} must be a canonical relative path")
    if path.as_posix() != path_value:
        raise ValidationError(f"{field} must be a canonical relative path")
    return path_value


def validate_safe_json(value: Any, field: str, *, depth: int = 0) -> Any:
    """Validate report-only JSON without assigning it path or command semantics."""

    if depth > 8:
        raise ValidationError(f"{field} is nested too deeply")
    if value is None or isinstance(value, (str, int, bool)):
        if isinstance(value, str) and contains_control(value):
            raise ValidationError(f"{field} contains a control character")
        return value
    if isinstance(value, float):
        raise ValidationError(f"{field} must not contain floating-point values")
    if isinstance(value, list):
        return [
            validate_safe_json(item, f"{field}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = require_plain_string(key, f"{field} key")
            result[safe_key] = validate_safe_json(
                item, f"{field}.{safe_key}", depth=depth + 1
            )
        return result
    raise ValidationError(f"{field} contains an unsupported JSON type")


def validate_oid_shape(value: Any, field: str) -> str | None:
    if value is None:
        return None
    oid = require_plain_string(value, field)
    if not HEX_OID.fullmatch(oid):
        raise ValidationError(f"{field} must be null or a complete 40/64-character hex OID")
    return oid.lower()


def normalize_commit(value: Any, repo: Path | None, field: str) -> str | None:
    oid = validate_oid_shape(value, field)
    if oid is None:
        return None
    if repo is None:
        raise ValidationError(f"{field} needs --source-root for commit verification")
    if repo.is_symlink() or not repo.is_dir():
        raise ValidationError(f"commit repository for {field} must be a real directory")
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{oid}^{{commit}}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ValidationError(f"cannot verify {field}: {exc}") from exc
    normalized = result.stdout.strip()
    if result.returncode != 0 or not HEX_OID.fullmatch(normalized):
        raise ValidationError(f"{field} does not resolve to an existing commit object")
    return normalized.lower()


def measured_scope_root(source_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--show-prefix"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ValidationError(f"cannot measure --source-root scope: {exc}") from exc
    if result.returncode != 0:
        raise ValidationError("cannot measure --source-root scope with Git")
    prefix = result.stdout.removesuffix("\n")
    return prefix.removesuffix("/") or "."


def validate_root_meta(value: dict[str, Any]) -> dict[str, Any]:
    anchor_kind = value.get("anchor_kind")
    if anchor_kind == "main":
        require_exact_keys(
            value,
            "<KB>/_meta.json",
            {"anchor_kind", "branch"},
            {"degraded_acknowledged"},
        )
        result: dict[str, Any] = {
            "anchor_kind": "main",
            "branch": validate_branch_name(value["branch"], "<KB>/_meta.json.branch"),
        }
    elif anchor_kind == "non-git":
        require_exact_keys(
            value,
            "<KB>/_meta.json",
            {"anchor_kind", "degraded_acknowledged"},
        )
        result = {"anchor_kind": "non-git"}
    else:
        raise ValidationError("<KB>/_meta.json.anchor_kind must be 'main' or 'non-git'")
    if "degraded_acknowledged" in value:
        if not isinstance(value["degraded_acknowledged"], bool):
            raise ValidationError("<KB>/_meta.json.degraded_acknowledged must be boolean")
        result["degraded_acknowledged"] = value["degraded_acknowledged"]
    return result


def validate_branch_meta(
    value: dict[str, Any],
    branch: str,
    kb: Path,
    field: str,
    *,
    archived_outcome: str | None = None,
    require_commit_objects: bool = True,
) -> tuple[dict[str, Any], str | None]:
    require_exact_keys(
        value,
        field,
        (
            {"anchor_kind", "branch", "forked_from", "outcome"}
            if archived_outcome is not None
            else {"anchor_kind", "branch", "forked_from"}
        ),
    )
    if value["anchor_kind"] != "branch":
        raise ValidationError(f"{field}.anchor_kind must be 'branch'")
    meta_branch = validate_branch_name(value["branch"], f"{field}.branch")
    if meta_branch != branch:
        raise ValidationError(f"{field}.branch does not match _branches.json key {branch!r}")
    if archived_outcome is not None:
        if value.get("outcome") != archived_outcome:
            raise ValidationError(f"{field}.outcome does not match _branches.json outcome")
    normalized_fork = (
        normalize_commit(value["forked_from"], kb, f"{field}.forked_from")
        if require_commit_objects
        else validate_oid_shape(value["forked_from"], f"{field}.forked_from")
    )
    result = {
        "anchor_kind": "branch",
        "branch": meta_branch,
        "forked_from": value["forked_from"],
    }
    if "outcome" in value:
        result["outcome"] = value["outcome"]
    return result, normalized_fork


def validate_sync(
    value: dict[str, Any], source_root: Path | None, field: str, *, require_commit: bool = True
) -> tuple[dict[str, Any], str | None]:
    require_exact_keys(value, field, SYNC_KEYS)
    version = value["codewise_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != CODEWISE_VERSION:
        raise ValidationError(f"{field}.codewise_version must be {CODEWISE_VERSION}")
    normalized_baseline = (
        normalize_commit(value["baseline_commit"], source_root, f"{field}.baseline_commit")
        if require_commit
        else validate_oid_shape(value["baseline_commit"], f"{field}.baseline_commit")
    )
    synced_at = validate_timestamp(value["synced_at"], f"{field}.synced_at", nullable=True)
    scope_root = validate_scope_root(value["scope_root"], f"{field}.scope_root")
    if not isinstance(value["multi_codetree"], list):
        raise ValidationError(f"{field}.multi_codetree must be an array")
    multi_codetree = validate_safe_json(value["multi_codetree"], f"{field}.multi_codetree")
    session_sources = validate_safe_json(value["session_sources"], f"{field}.session_sources")
    worktree_count = value["worktree_count"]
    if isinstance(worktree_count, bool) or not isinstance(worktree_count, int) or worktree_count < 0:
        raise ValidationError(f"{field}.worktree_count must be a non-negative integer")
    known_value = value["known_worktrees"]
    if not isinstance(known_value, list):
        raise ValidationError(f"{field}.known_worktrees must be an array")
    known_worktrees = [
        validate_known_worktree(item, f"{field}.known_worktrees[{index}]")
        for index, item in enumerate(known_value)
    ]
    if len(known_worktrees) != len(set(known_worktrees)):
        raise ValidationError(f"{field}.known_worktrees must not contain duplicates")
    return (
        {
            "codewise_version": version,
            "baseline_commit": value["baseline_commit"],
            "synced_at": synced_at,
            "scope_root": scope_root,
            "multi_codetree": multi_codetree,
            "session_sources": session_sources,
            "worktree_count": worktree_count,
            "known_worktrees": known_worktrees,
        },
        normalized_baseline,
    )


def validate_branches_schema(
    value: dict[str, Any], main_branch: str | None
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    slug_owners: dict[str, str] = {}
    for raw_branch, raw_registration in value.items():
        branch = validate_branch_name(raw_branch, "_branches.json branch key")
        if main_branch is None:
            raise ValidationError("non-Git knowledge bases cannot register branches")
        if branch == main_branch:
            raise ValidationError("_branches.json must not register the main anchor branch")
        if not isinstance(raw_registration, dict):
            raise ValidationError(f"_branches.json[{branch!r}] must be an object")
        mode = raw_registration.get("mode")
        if mode == "inherit":
            require_exact_keys(
                raw_registration,
                f"_branches.json[{branch!r}]",
                {"mode", "parent_anchor", "confirmed_at"},
                {"archive_history"},
            )
        elif mode == "independent":
            require_exact_keys(
                raw_registration,
                f"_branches.json[{branch!r}]",
                {"mode", "parent_anchor", "slug", "confirmed_at"},
                {"archive_history"},
            )
        elif mode == "archived":
            require_exact_keys(
                raw_registration,
                f"_branches.json[{branch!r}]",
                {
                    "mode",
                    "parent_anchor",
                    "slug",
                    "confirmed_at",
                    "archive_path",
                    "archived_at",
                    "outcome",
                },
                {"archive_history"},
            )
        else:
            raise ValidationError(
                f"_branches.json[{branch!r}].mode must be 'independent', "
                "'inherit', or 'archived'"
            )
        parent = validate_branch_name(
            raw_registration["parent_anchor"],
            f"_branches.json[{branch!r}].parent_anchor",
        )
        if parent != main_branch:
            raise ValidationError(
                f"_branches.json[{branch!r}].parent_anchor must equal the main anchor"
            )
        confirmed_at = validate_timestamp(
            raw_registration["confirmed_at"],
            f"_branches.json[{branch!r}].confirmed_at",
            nullable=False,
        )
        registration: dict[str, Any] = {
            "mode": mode,
            "parent_anchor": parent,
            "confirmed_at": confirmed_at,
        }
        archive_history = validate_archive_history(
            raw_registration.get("archive_history"),
            branch,
            f"_branches.json[{branch!r}].archive_history",
        )
        if archive_history:
            registration["archive_history"] = archive_history
        if mode == "independent":
            slug = validate_slug(
                raw_registration["slug"], branch, f"_branches.json[{branch!r}].slug"
            )
            collision_key = slug.casefold()
            if collision_key in slug_owners:
                raise ValidationError(
                    f"independent slug {slug!r} is shared by {slug_owners[collision_key]!r} "
                    f"and {branch!r}"
                )
            slug_owners[collision_key] = branch
            registration["slug"] = slug
        if mode == "archived":
            archived_record = validate_archive_record(
                {
                    "archive_path": raw_registration["archive_path"],
                    "archived_at": raw_registration["archived_at"],
                    "outcome": raw_registration["outcome"],
                    "slug": raw_registration["slug"],
                },
                branch,
                f"_branches.json[{branch!r}]",
            )
            registration.update(
                {
                    "slug": archived_record["slug"],
                    "archive_path": archived_record["archive_path"],
                    "archived_at": archived_record["archived_at"],
                    "outcome": archived_record["outcome"],
                }
            )
        result[branch] = registration
    return result


def ensure_real_directory(path: Path, field: str) -> None:
    if path.is_symlink():
        raise ValidationError(f"{field} must not be a symlink")
    if not path.is_dir():
        raise ValidationError(f"{field} must be a real directory")


def ensure_direct_branch_root(kbr: Path, kb: Path, field: str) -> str:
    branches_root = kb / ".branches"
    if branches_root.is_symlink():
        raise ValidationError("<KB>/.branches must not be a symlink")
    ensure_real_directory(kbr, field)
    try:
        relative = kbr.relative_to(branches_root)
    except ValueError as exc:
        raise ValidationError(f"{field} must be below <KB>/.branches") from exc
    if len(relative.parts) != 1:
        raise ValidationError(f"{field} must be a direct child of <KB>/.branches")
    try:
        if kbr.resolve(strict=True).parent != branches_root.resolve(strict=True):
            raise ValidationError(f"{field} resolves outside <KB>/.branches")
    except OSError as exc:
        raise ValidationError(f"cannot resolve {field}: {exc}") from exc
    return relative.parts[0]


def validate_archive_directory(
    kb: Path,
    archive_root: Path,
    branch: str,
    record: dict[str, Any],
    source_root: Path | None,
    scope_root: str,
    field: str,
) -> dict[str, Any]:
    archive_path = kb / record["archive_path"]
    ensure_real_directory(archive_path, field)
    try:
        if archive_path.resolve(strict=True).parent != archive_root.resolve(strict=True):
            raise ValidationError(f"{field} resolves outside <KB>/.archive")
    except OSError as exc:
        raise ValidationError(f"cannot resolve {field}: {exc}") from exc
    if archive_path.name != PurePosixPath(record["archive_path"]).name:
        raise ValidationError(f"{field} is not canonical")
    reject_control_symlinks(archive_path, ARCHIVE_CONTROL_NAMES, field)
    meta, normalized_fork = validate_branch_meta(
        read_json_object(archive_path / "_meta.json", f"{field}/_meta.json"),
        branch,
        kb,
        f"{field}/_meta.json",
        archived_outcome=record["outcome"],
        require_commit_objects=False,
    )
    sync, normalized_baseline = validate_sync(
        read_json_object(archive_path / "_sync.json", f"{field}/_sync.json"),
        source_root,
        f"{field}/_sync.json",
        require_commit=False,
    )
    if sync["scope_root"] != scope_root:
        raise ValidationError(f"{field} scope_root differs from root _sync.json")
    return {
        "record": record,
        "path": str(archive_path),
        "write_allowed": False,
        "meta": meta,
        "sync": sync,
        "normalized": {
            "forked_from": normalized_fork,
            "baseline_commit": normalized_baseline,
        },
    }


def validate_control_state(
    kb_value: str | Path,
    kbr_value: str | Path | None = None,
    source_root_value: str | Path | None = None,
) -> dict[str, Any]:
    kb = lexical_absolute(kb_value)
    ensure_real_directory(kb, "<KB>")
    reject_control_symlinks(kb, ROOT_CONTROL_NAMES, "<KB>")

    source_root: Path | None = None
    if source_root_value is not None:
        source_root = lexical_absolute(source_root_value)
        ensure_real_directory(source_root, "--source-root")

    root_meta = validate_root_meta(read_json_object(kb / "_meta.json", "<KB>/_meta.json"))
    root_sync, root_baseline = validate_sync(
        read_json_object(kb / "_sync.json", "<KB>/_sync.json"),
        source_root,
        "<KB>/_sync.json",
    )
    raw_branches = read_json_object(kb / "_branches.json", "<KB>/_branches.json")
    registrations = validate_branches_schema(raw_branches, root_meta.get("branch"))
    if root_sync["scope_root"] != "." and root_meta["anchor_kind"] == "non-git":
        raise ValidationError("non-Git root _sync.json.scope_root must be '.'")
    if source_root is not None and root_meta["anchor_kind"] == "main":
        actual_scope = measured_scope_root(source_root)
        if root_sync["scope_root"] != actual_scope:
            raise ValidationError(
                "<KB>/_sync.json.scope_root differs from the measured --source-root scope"
            )

    branches_root = kb / ".branches"
    independent_slugs = {
        registration["slug"]
        for registration in registrations.values()
        if registration["mode"] == "independent"
    }
    independent_slug_owners = {
        registration["slug"].casefold(): branch
        for branch, registration in registrations.items()
        if registration["mode"] == "independent"
    }
    if branches_root.exists():
        ensure_real_directory(branches_root, "<KB>/.branches")
        try:
            children = list(branches_root.iterdir())
        except OSError as exc:
            raise ValidationError(f"cannot enumerate <KB>/.branches: {exc}") from exc
        actual: set[str] = set()
        for child in children:
            if child.is_symlink():
                raise ValidationError(f"branch root must not be a symlink: {child.name}")
            if not child.is_dir():
                raise ValidationError(f"unexpected non-directory below <KB>/.branches: {child.name}")
            actual.add(child.name)
        unexpected = sorted(actual - independent_slugs)
        missing = sorted(independent_slugs - actual)
        if unexpected:
            raise ValidationError(
                "unregistered branch directories below <KB>/.branches: " + ", ".join(unexpected)
            )
        if missing:
            raise ValidationError("registered branch directories are missing: " + ", ".join(missing))
    elif independent_slugs:
        raise ValidationError("<KB>/.branches is missing for independent registrations")

    archive_root = kb / ".archive"
    all_archive_records: list[tuple[str, dict[str, Any], str]] = []
    for branch, registration in registrations.items():
        for index, record in enumerate(registration.get("archive_history", [])):
            all_archive_records.append((branch, record, f"archive history {branch!r}[{index}]"))
        if registration["mode"] == "archived":
            all_archive_records.append(
                (
                    branch,
                    {
                        "slug": registration["slug"],
                        "archive_path": registration["archive_path"],
                        "archived_at": registration["archived_at"],
                        "outcome": registration["outcome"],
                    },
                    f"archive {branch!r}",
                )
            )
    archived_paths = {
        record["archive_path"].casefold() for _, record, _ in all_archive_records
    }
    if len(archived_paths) != len(all_archive_records):
        raise ValidationError("archived registrations must have globally unique archive_path values")
    expected_archive_names = {
        PurePosixPath(record["archive_path"]).name for _, record, _ in all_archive_records
    }
    if archive_root.exists():
        ensure_real_directory(archive_root, "<KB>/.archive")
        try:
            archive_children = list(archive_root.iterdir())
        except OSError as exc:
            raise ValidationError(f"cannot enumerate <KB>/.archive: {exc}") from exc
        actual_archive_names: set[str] = set()
        for child in archive_children:
            if child.is_symlink():
                raise ValidationError(f"archive root must not be a symlink: {child.name}")
            if not child.is_dir():
                raise ValidationError(
                    f"unexpected non-directory below <KB>/.archive: {child.name}"
                )
            actual_archive_names.add(child.name)
        unexpected_archives = sorted(actual_archive_names - expected_archive_names)
        missing_archives = sorted(expected_archive_names - actual_archive_names)
        if unexpected_archives:
            raise ValidationError(
                "unregistered archive directories below <KB>/.archive: "
                + ", ".join(unexpected_archives)
            )
        if missing_archives:
            raise ValidationError(
                "registered archive directories are missing: " + ", ".join(missing_archives)
            )
    elif all_archive_records:
        raise ValidationError("<KB>/.archive is missing for archived registrations")

    normalized_branches: dict[str, Any] = {}
    scope_root = root_sync["scope_root"]
    for branch, registration in registrations.items():
        item: dict[str, Any] = {"registration": registration}
        validated_history = [
            validate_archive_directory(
                kb,
                archive_root,
                branch,
                record,
                source_root,
                scope_root,
                f"archive history {branch!r}[{index}]",
            )
            for index, record in enumerate(registration.get("archive_history", []))
        ]
        if validated_history:
            item["archive_history"] = validated_history
        if registration["mode"] == "independent":
            branch_root = branches_root / registration["slug"]
            ensure_direct_branch_root(branch_root, kb, f"branch root for {branch!r}")
            reject_control_symlinks(branch_root, BRANCH_CONTROL_NAMES, f"branch {branch!r}")
            meta, normalized_fork = validate_branch_meta(
                read_json_object(branch_root / "_meta.json", f"branch {branch!r}/_meta.json"),
                branch,
                kb,
                f"branch {branch!r}/_meta.json",
            )
            sync, normalized_baseline = validate_sync(
                read_json_object(branch_root / "_sync.json", f"branch {branch!r}/_sync.json"),
                source_root,
                f"branch {branch!r}/_sync.json",
            )
            if sync["scope_root"] != scope_root:
                raise ValidationError(f"branch {branch!r} scope_root differs from root _sync.json")
            item.update(
                {
                    "path": str(branch_root),
                    "meta": meta,
                    "sync": sync,
                    "normalized": {
                        "forked_from": normalized_fork,
                        "baseline_commit": normalized_baseline,
                    },
                }
            )
        elif registration["mode"] == "archived":
            active_root = branches_root / registration["slug"]
            active_owner = independent_slug_owners.get(registration["slug"].casefold())
            if (active_root.exists() or active_root.is_symlink()) and active_owner is None:
                raise ValidationError(
                    f"archived branch {branch!r} still has an active .branches directory"
                )
            archived = validate_archive_directory(
                kb,
                archive_root,
                branch,
                {
                    "slug": registration["slug"],
                    "archive_path": registration["archive_path"],
                    "archived_at": registration["archived_at"],
                    "outcome": registration["outcome"],
                },
                source_root,
                scope_root,
                f"archive {branch!r}",
            )
            archived["requires_reconfirmation"] = True
            item.update(archived)
        normalized_branches[branch] = item

    selected: dict[str, Any]
    if kbr_value is None:
        selected = {
            "kind": "main" if root_meta["anchor_kind"] == "main" else "non-git",
            "branch": root_meta.get("branch"),
            "path": str(kb),
        }
        kbr: Path | None = None
    else:
        kbr = lexical_absolute(kbr_value)
        slug = ensure_direct_branch_root(kbr, kb, "<KBR>")
        matches = [
            branch
            for branch, registration in registrations.items()
            if registration.get("mode") == "independent" and registration.get("slug") == slug
        ]
        if len(matches) != 1:
            raise ValidationError("<KBR> is not owned by exactly one independent branch")
        branch = matches[0]
        if normalized_branches[branch]["meta"]["branch"] != branch:
            raise ValidationError("<KBR>/_meta.json.branch does not match its registration")
        selected = {"kind": "branch", "branch": branch, "path": str(kbr)}

    return {
        "schema_version": 1,
        "kb": str(kb),
        "kbr": str(kbr) if kbr is not None else None,
        "source_root": str(source_root) if source_root is not None else None,
        "root": {
            "meta": root_meta,
            "sync": root_sync,
            "normalized": {"baseline_commit": root_baseline},
        },
        "branches": normalized_branches,
        "selected": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", required=True, help="Canonical Codewise knowledge repository")
    parser.add_argument("--kbr", help="Optional <KB>/.branches/<slug> to select")
    parser.add_argument(
        "--source-root",
        help="Project Git repository used to verify baseline_commit values",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()
    try:
        result = validate_control_state(args.kb, args.kbr, args.source_root)
    except ValidationError as exc:
        print(f"control state invalid: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # Keep hostile input from exposing an implementation traceback.
        print(f"control state invalid: unexpected validation failure: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
