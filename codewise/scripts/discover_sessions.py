#!/usr/bin/env python3
"""Discover project-related coding-agent sessions across Git worktrees.

The script only reads lightweight session metadata.  It deliberately leaves the
final, content-level relevance check to Codewise because a session started at a
repository root may only concern a sibling subproject.

Usage:
    python3 discover_sessions.py ROOT [--since ISO8601] [--baseline COMMIT] [--pretty]
    python3 discover_sessions.py ROOT --source 'continue=~/.continue/sessions/**/*.json'
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Iterable


BUILTIN_PROVIDERS = ("claude", "codex", "gemini", "opencode", "cursor", "aider")


def env_path(name: str, default: str) -> Path:
    return Path(os.path.expanduser(os.environ.get(name, default)))


def lexical_path(value: str | Path) -> Path:
    """Return a normalized absolute path without requiring it to still exist."""
    # resolve(strict=False) also normalizes macOS aliases such as /var ->
    # /private/var while preserving a useful path for removed worktrees.
    return Path(os.path.expanduser(str(value))).resolve(strict=False)


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def relative_to_repo(path: Path, base_root: Path) -> str:
    """Express a worktree path relative to the MAIN worktree root.

    The base must be the main worktree, not `--show-toplevel`: inside a linked
    worktree that returns the linked worktree's own root, while the knowledge
    base lives under `git-common-dir` and is shared by every worktree.  Mixing
    the two bases makes entries written from one tree resolve to garbage when
    read back from another — both inventing ghost paths and, worse, corrupting
    real ones so their sessions become undiscoverable.
    """
    try:
        return os.path.relpath(path, base_root)
    except ValueError:
        # Different drive on Windows: nothing relative exists, keep it absolute.
        return str(path)


def git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def project_context(
    root: Path,
    baseline: str | None = None,
    known_worktrees: Iterable[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    root = lexical_path(root)
    top_text = git(root, "rev-parse", "--show-toplevel")
    if not top_text:
        return ({
            "root": str(root),
            "repo_root": None,
            "git_common_dir": None,
            "scope_rel": ".",
            "worktrees": [{"root": str(root), "scope_root": str(root), "head": None, "branch": None}],
            "known_worktrees": ["."],
        }, [])

    repo_root = lexical_path(top_text)
    try:
        scope_rel = root.relative_to(repo_root)
    except ValueError:
        scope_rel = Path(".")

    common_text = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common_text:
        common_dir = lexical_path(common_text)
    else:
        fallback = git(root, "rev-parse", "--git-common-dir")
        common_dir = lexical_path(repo_root / fallback) if fallback else None

    worktrees: list[dict[str, Any]] = []
    porcelain = git(root, "worktree", "list", "--porcelain") or ""
    current: dict[str, Any] = {}
    for line in [*porcelain.splitlines(), ""]:
        if not line:
            if current.get("root"):
                wt_root = lexical_path(current["root"])
                current["scope_root"] = str(lexical_path(wt_root / scope_rel))
                worktrees.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["root"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key in ("detached", "bare"):
            current[key] = True

    if not worktrees:
        worktrees.append(
            {
                "root": str(repo_root),
                "scope_root": str(lexical_path(repo_root / scope_rel)),
                "head": git(root, "rev-parse", "HEAD"),
                "branch": git(root, "branch", "--show-current") or None,
            }
        )

    # `git worktree list` puts the main worktree first.  It — not
    # `--show-toplevel`, which follows whichever tree we happen to run in — is
    # the stable base for persisted relative paths, because the knowledge base
    # is shared across all worktrees.
    main_root = next(
        (lexical_path(wt["root"]) for wt in worktrees if not wt.get("bare")),
        repo_root,
    )

    # Worktrees removed with `git worktree remove`/`prune` disappear from the
    # registry, and with them every session whose cwd lived inside them.  Paths
    # recorded by earlier runs are re-admitted as stale candidates so their
    # sessions stay discoverable; they carry no HEAD/branch because the
    # directory may be gone, which keeps downstream checks conservative.
    seen_roots = {lexical_path(wt["root"]) for wt in worktrees}
    for entry in known_worktrees or []:
        # Entries persisted in the knowledge base are relative to the main
        # worktree so no username-bearing absolute path is ever committed.
        raw = Path(os.path.expanduser(entry))
        entry_root = lexical_path(raw if raw.is_absolute() else main_root / raw)
        if entry_root in seen_roots:
            continue
        seen_roots.add(entry_root)
        worktrees.append(
            {
                "root": str(entry_root),
                "scope_root": str(lexical_path(entry_root / scope_rel)),
                "head": None,
                "branch": None,
                "stale": True,
            }
        )

    warnings: list[str] = []
    if baseline:
        # `git cat-file -e` succeeds with empty stdout, which the lightweight
        # git() helper represents as an empty string.  Resolve to a commit
        # object instead so availability is tested by a non-empty result.
        baseline_commit = git(root, "rev-parse", f"{baseline}^{{commit}}")
        if not baseline_commit:
            warnings.append(f"baseline commit is not available: {baseline}")
        else:
            for worktree in worktrees:
                head = worktree.get("head")
                if head:
                    worktree["merge_base"] = git(Path(worktree["root"]), "merge-base", baseline_commit, head)
                    if not worktree["merge_base"]:
                        warnings.append(
                            f"cannot compute merge-base for worktree {worktree['root']} and baseline {baseline}"
                        )

    return ({
        "root": str(root),
        "repo_root": str(repo_root),
        "git_common_dir": str(common_dir) if common_dir else None,
        "scope_rel": scope_rel.as_posix() or ".",
        "worktrees": worktrees,
        "main_worktree": str(main_root),
        # Full set the caller should persist and pass back next time, expressed
        # relative to the MAIN worktree so it resolves identically no matter
        # which worktree the next run happens in.
        "known_worktrees": sorted(relative_to_repo(path, main_root) for path in seen_roots),
    }, warnings)


def match_cwd(cwd_value: str | None, context: dict[str, Any]) -> dict[str, Any] | None:
    if not cwd_value:
        return None
    cwd = lexical_path(cwd_value)
    # Worktrees can be nested inside the main tree (`.claude/worktrees/<name>`
    # is the default for worktrees Claude Code creates).  Matching the first
    # containing root would attribute those sessions to the parent tree — wrong
    # branch, and `needs_content_check=False` would skip verification too.
    # Deepest path wins so the innermost worktree claims its own sessions.
    ordered = sorted(
        context["worktrees"],
        key=lambda wt: len(lexical_path(wt["root"]).parts),
        reverse=True,
    )
    for wt in ordered:
        wt_root = lexical_path(wt["root"])
        scope_root = lexical_path(wt["scope_root"])
        if is_within(cwd, scope_root):
            return {
                "relation": "scope-cwd",
                "worktree_root": str(wt_root),
                "scope_root": str(scope_root),
                "needs_content_check": False,
            }
        if is_within(cwd, wt_root):
            return {
                "relation": "worktree-cwd",
                "worktree_root": str(wt_root),
                "scope_root": str(scope_root),
                "needs_content_check": True,
            }
    return None


def parse_since(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO 8601 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def normalize_branch(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    branch = value.strip()
    if branch.startswith("refs/heads/"):
        branch = branch.removeprefix("refs/heads/")
    # A detached HEAD has no branch name, but clients record the literal
    # "HEAD" for it.  Treating that as a branch would both fake a branch
    # switch (main -> HEAD) and let a non-branch reach attribution.
    if branch == "HEAD":
        return None
    return branch or None


def metadata_from_jsonl(path: Path, max_lines: int = 128) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= max_lines:
                    break
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                git_payload = payload.get("git") if isinstance(payload.get("git"), dict) else {}
                for key in (
                    "cwd",
                    "sessionId",
                    "session_id",
                    "id",
                    "timestamp",
                    "gitBranch",
                    "git_branch",
                    "branch",
                ):
                    if key in item and item[key] is not None:
                        metadata.setdefault(key, item[key])
                    if key in payload and payload[key] is not None:
                        metadata.setdefault(key, payload[key])
                if item.get("type") == "session_meta":
                    metadata["session_id"] = payload.get("session_id") or payload.get("id")
                    metadata["cwd"] = payload.get("cwd")
                    metadata["originator"] = payload.get("originator")
                    metadata["source"] = payload.get("source")
                if item.get("gitBranch"):
                    metadata.setdefault("branch", item["gitBranch"])
                elif payload.get("gitBranch"):
                    metadata.setdefault("branch", payload["gitBranch"])
                elif item.get("git_branch"):
                    metadata.setdefault("branch", item["git_branch"])
                elif payload.get("git_branch"):
                    metadata.setdefault("branch", payload["git_branch"])
                elif item.get("branch"):
                    metadata.setdefault("branch", item["branch"])
                elif payload.get("branch"):
                    metadata.setdefault("branch", payload["branch"])
                elif git_payload.get("branch"):
                    metadata.setdefault("branch", git_payload["branch"])
                if git_payload.get("commit_hash"):
                    metadata.setdefault("commit_hash", git_payload["commit_hash"])
                # Claude can write cwd/session id before gitBranch.  Keep
                # scanning the bounded prefix until the historical branch is
                # found; otherwise we would incorrectly label a known branch
                # as unknown-branch.
                if (
                    metadata.get("cwd")
                    and (metadata.get("session_id") or metadata.get("sessionId"))
                    and metadata.get("branch")
                ):
                    break
    except OSError:
        pass
    return metadata


BRANCH_HINTS = ('"gitBranch"', '"git_branch"', '"branch"')


def branch_timeline(path: Path) -> list[str]:
    """Collect every branch a session records, in first-seen order.

    Sessions get resumed, so one file can span several branches — measured on
    this machine, 1.9% of Claude sessions do, one of them across six.  Claude
    stamps `gitBranch` on every user/assistant message, so its list is
    message-accurate.  Codex only writes `payload.git.branch` at each
    start/resume, so its list marks resume points, not every turn: a branch
    switch made mid-segment from another terminal leaves no trace.

    Callers must attribute signals per segment rather than per session; a
    single `branch` value cannot define a session that spans several.
    """
    seen: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                # Cheap prefilter — most lines carry no branch field at all.
                if not any(hint in line for hint in BRANCH_HINTS):
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                git_payload = payload.get("git") if isinstance(payload.get("git"), dict) else {}
                for candidate in (
                    item.get("gitBranch"),
                    payload.get("gitBranch"),
                    item.get("git_branch"),
                    payload.get("git_branch"),
                    item.get("branch"),
                    payload.get("branch"),
                    git_payload.get("branch"),
                ):
                    branch = normalize_branch(candidate)
                    if branch and branch not in seen:
                        seen.append(branch)
    except OSError:
        pass
    return seen


def session_item(
    provider: str,
    path: Path,
    fmt: str,
    context: dict[str, Any],
    *,
    cwd: str | None = None,
    match: dict[str, Any] | None = None,
    session_id: str | None = None,
    parent_session_id: str | None = None,
    branch: str | None = None,
    commit: str | None = None,
    selector: str | None = None,
    needs_content_check: bool | None = None,
    branches: list[str] | None = None,
) -> dict[str, Any]:
    stat = path.stat()
    worktree = next(
        (wt for wt in context["worktrees"] if wt.get("root") == (match or {}).get("worktree_root")),
        None,
    )
    scan_branch = normalize_branch(worktree.get("branch")) if worktree else None
    scan_head = worktree.get("head") if worktree else None
    session_branch = normalize_branch(branch)
    session_head = commit.strip() if isinstance(commit, str) and commit.strip() else None
    item: dict[str, Any] = {
        "provider": provider,
        "path": str(path),
        "format": fmt,
        "session_id": session_id,
        "parent_session_id": parent_session_id,
        "branch": session_branch,
        "branch_source": "session-metadata" if session_branch else "unknown",
        "branch_state": (
            "session-branch-matches-scan"
            if session_branch and scan_branch and session_branch == scan_branch
            else "session-branch-differs-from-scan"
            if session_branch and scan_branch and session_branch != scan_branch
            else "unknown-branch"
        ),
        "scan_branch": scan_branch,
        "scan_head": scan_head,
        # Every branch this session recorded.  More than one means the session
        # was resumed across a branch switch, so signals must be attributed
        # per segment — a single `branch` value would mislabel most of it.
        "branches": list(branches) if branches else ([session_branch] if session_branch else []),
        "multi_branch": bool(branches and len(branches) > 1),
        "session_head": session_head,
        "head_state": (
            "session-head-matches-scan"
            if session_head and scan_head and session_head == scan_head
            else "session-head-differs-from-scan"
            if session_head and scan_head and session_head != scan_head
            else "unknown-head"
        ),
        "merge_base": worktree.get("merge_base") if worktree else None,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "cwd": str(lexical_path(cwd)) if cwd else None,
        "relation": (match or {}).get("relation", "content-candidate"),
        "worktree_root": (match or {}).get("worktree_root"),
        "scope_root": (match or {}).get("scope_root"),
        "needs_content_check": (
            needs_content_check
            if needs_content_check is not None
            else (match or {}).get("needs_content_check", True)
        ),
    }
    if selector:
        item["selector"] = selector
    return item


def after_since(path: Path, since: float | None) -> bool:
    try:
        return since is None or path.stat().st_mtime > since
    except OSError:
        return False


def claude_slug(path: Path) -> str:
    # Claude Code replaces every character outside [A-Za-z0-9] with a hyphen, so
    # `/Users/x/p/.claude/worktrees/w` becomes `-Users-x-p--claude-worktrees-w`.
    # Replacing only os.sep silently misses every path containing a dot, which
    # includes `.claude/worktrees/` — the default location for worktrees Claude
    # Code creates itself.
    return re.sub(r"[^A-Za-z0-9]", "-", str(lexical_path(path)))


def is_derived_transcript(path: Path, base: Path) -> bool:
    """True for records produced by a workflow run rather than a session.

    A workflow writes `wf_<id>/agent-<id>.jsonl` (one per subagent it spawned)
    and `wf_<id>/journal.jsonl` (a structured run log, not a transcript at all)
    into the project directory.  Measured on a real project: 12 of 13 "sessions"
    were these.  Treating each as an independent session inflates the count an
    order of magnitude and, since a subagent's conclusions already flow back
    into the parent session, re-reads the same work N times.
    """
    try:
        rel = path.relative_to(base)
    except ValueError:
        return False
    return any(part.startswith("wf_") for part in rel.parts[:-1]) and (
        rel.name == "journal.jsonl" or rel.name.startswith("agent-")
    )


def discover_claude(
    context: dict[str, Any],
    since: float | None,
    include_derived: bool = False,
) -> list[dict[str, Any]]:
    claude_default = os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")
    base = env_path("CODEWISE_CLAUDE_HOME", claude_default) / "projects"
    if not base.is_dir():
        return []

    # Which project directory a file sits in is stronger evidence than the cwd
    # recorded inside it.  Claude Code derives the directory from the cwd the
    # session started in, whereas `metadata_from_jsonl` returns the FIRST cwd
    # in the file — for a session that entered a worktree partway through (or
    # was resumed there) those disagree, and the first cwd attributes the whole
    # session to the parent tree.
    candidates: dict[Path, dict[str, Any] | None] = {}
    for wt in context["worktrees"]:
        wt_root = lexical_path(wt["root"])
        scope_root = lexical_path(wt["scope_root"])
        # scope first: it is the more specific of the two.
        for start_root, relation in ((scope_root, "scope-project-dir"), (wt_root, "worktree-project-dir")):
            project_dir = base / claude_slug(start_root)
            if not project_dir.is_dir():
                continue
            origin = {
                "relation": relation,
                "worktree_root": str(wt_root),
                "scope_root": str(scope_root),
                "needs_content_check": relation != "scope-project-dir",
            }
            for path in project_dir.rglob("*.jsonl"):
                if not include_derived and is_derived_transcript(path, base):
                    continue
                candidates.setdefault(path, origin)

    # Catch sessions launched from a nested directory whose slug cannot be
    # derived from ROOT.  Only the first metadata records are read.
    prefetched: dict[Path, dict[str, Any]] = {}
    for path in base.glob("*/*.jsonl"):
        if path in candidates or not after_since(path, since):
            continue
        metadata = metadata_from_jsonl(path)
        if match_cwd(metadata.get("cwd"), context):
            candidates[path] = None
            prefetched[path] = metadata

    sessions: list[dict[str, Any]] = []
    for path in sorted(candidates):
        if not after_since(path, since):
            continue
        metadata = prefetched.get(path) or metadata_from_jsonl(path)
        # Directory origin wins; the recorded cwd is the fallback.
        match = candidates[path] or match_cwd(metadata.get("cwd"), context)
        if not match:
            # Nested side-agent files inherit relevance from the selected
            # project directory and still require content confirmation.
            match = {"relation": "project-directory", "needs_content_check": True}
        sessions.append(
            session_item(
                "claude",
                path,
                "claude-jsonl",
                context,
                cwd=metadata.get("cwd"),
                match=match,
                session_id=metadata.get("sessionId") or metadata.get("session_id"),
                branch=metadata.get("branch"),
                commit=metadata.get("commit_hash"),
                # Only scanned for sessions that already passed attribution, so
                # the full-file read stays cheap.
                branches=branch_timeline(path),
            )
        )
    return sessions


def discover_codex(context: dict[str, Any], since: float | None) -> list[dict[str, Any]]:
    codex_default = os.environ.get("CODEX_HOME", "~/.codex")
    home = env_path("CODEWISE_CODEX_HOME", codex_default)
    files: set[Path] = set()
    for dirname in ("sessions", "archived_sessions"):
        base = home / dirname
        if base.is_dir():
            files.update(base.rglob("*.jsonl"))

    sessions: list[dict[str, Any]] = []
    for path in sorted(files):
        if not after_since(path, since):
            continue
        metadata = metadata_from_jsonl(path, max_lines=32)
        match = match_cwd(metadata.get("cwd"), context)
        if not match:
            continue
        sessions.append(
            session_item(
                "codex",
                path,
                "codex-rollout-jsonl",
                context,
                cwd=metadata.get("cwd"),
                match=match,
                # payload.id identifies this rollout/thread. payload.session_id
                # can point at the root thread and is shared by child agents.
                session_id=metadata.get("id") or metadata.get("session_id"),
                parent_session_id=(
                    metadata.get("session_id")
                    if metadata.get("session_id") != metadata.get("id")
                    else None
                ),
                branch=metadata.get("branch"),
                commit=metadata.get("commit_hash"),
                # Codex records git provenance at each start/resume, so this
                # marks resume points rather than every turn.
                branches=branch_timeline(path),
            )
        )
    return sessions


def gemini_hash(path: Path) -> str:
    return hashlib.sha256(str(lexical_path(path)).encode()).hexdigest()


def discover_gemini(context: dict[str, Any], since: float | None) -> list[dict[str, Any]]:
    gemini_base = Path(os.environ.get("GEMINI_CLI_HOME", str(Path.home()))) / ".gemini"
    base = env_path("CODEWISE_GEMINI_HOME", str(gemini_base)) / "tmp"
    if not base.is_dir():
        return []

    roots: dict[str, tuple[Path, dict[str, Any]]] = {}
    for wt in context["worktrees"]:
        wt_root = Path(wt["root"])
        scope_root = Path(wt["scope_root"])
        roots[gemini_hash(scope_root)] = (
            scope_root,
            {
                "relation": "scope-project-hash",
                "worktree_root": str(wt_root),
                "scope_root": str(scope_root),
                "needs_content_check": False,
            },
        )
        roots[gemini_hash(wt_root)] = (
            wt_root,
            {
                "relation": "worktree-project-hash",
                "worktree_root": str(wt_root),
                "scope_root": str(scope_root),
                "needs_content_check": scope_root != wt_root,
            },
        )

    sessions: list[dict[str, Any]] = []
    for digest, (cwd, match) in roots.items():
        chats = base / digest / "chats"
        if not chats.is_dir():
            continue
        for path in sorted(chats.glob("session-*.json")):
            if not after_since(path, since):
                continue
            session_id = None
            branch = None
            commit = None
            try:
                with path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    session_id = data.get("sessionId")
                    git_data = data.get("git") if isinstance(data.get("git"), dict) else {}
                    branch = data.get("branch") or data.get("gitBranch") or data.get("git_branch") or git_data.get("branch")
                    commit = (
                        data.get("commit_hash")
                        or data.get("commit")
                        or data.get("git_commit")
                        or git_data.get("commit_hash")
                    )
            except (OSError, json.JSONDecodeError):
                pass
            sessions.append(
                session_item(
                    "gemini",
                    path,
                    "gemini-session-json",
                    context,
                    cwd=str(cwd),
                    match=match,
                    session_id=session_id,
                    branch=branch,
                    commit=commit,
                )
            )
    return sessions


def find_column(columns: Iterable[str], *names: str) -> str | None:
    lookup = {column.lower(): column for column in columns}
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return None


def discover_opencode_db(
    database: Path, context: dict[str, Any], since: float | None
) -> list[dict[str, Any]]:
    if not after_since(database, since):
        return []
    sessions: list[dict[str, Any]] = []
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        session_table = next((name for name in ("session", "sessions") if name in tables), None)
        if not session_table:
            return []
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{session_table}")')]
        id_column = find_column(columns, "id", "session_id", "sessionID")
        cwd_column = find_column(columns, "directory", "cwd", "path", "worktree")
        branch_column = find_column(columns, "branch", "git_branch", "gitBranch")
        commit_column = find_column(columns, "commit_hash", "commit", "git_commit")
        if not id_column or not cwd_column:
            return []
        fields = [f'"{id_column}" AS session_id', f'"{cwd_column}" AS cwd']
        fields.append(f'"{branch_column}" AS branch' if branch_column else "NULL AS branch")
        fields.append(f'"{commit_column}" AS commit_hash' if commit_column else "NULL AS commit_hash")
        query = f'SELECT {", ".join(fields)} FROM "{session_table}"'
        for row in connection.execute(query):
            match = match_cwd(row["cwd"], context)
            if not match:
                continue
            sessions.append(
                session_item(
                    "opencode",
                    database,
                    "opencode-sqlite",
                    context,
                    cwd=row["cwd"],
                    match=match,
                    session_id=str(row["session_id"]),
                    branch=row["branch"],
                    commit=row["commit_hash"],
                    selector=f"{session_table}.{id_column}={row['session_id']}",
                )
            )
    except sqlite3.Error:
        return []
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass
    return sessions


def discover_opencode(context: dict[str, Any], since: float | None) -> list[dict[str, Any]]:
    home = env_path("CODEWISE_OPENCODE_HOME", "~/.local/share/opencode")
    sessions: list[dict[str, Any]] = []
    database = home / "opencode.db"
    if database.is_file():
        sessions.extend(discover_opencode_db(database, context, since))

    storage = home / "storage" / "session"
    if storage.is_dir():
        for path in sorted(storage.rglob("*.json")):
            if not after_since(path, since):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            cwd = data.get("directory") or data.get("cwd")
            if not cwd and isinstance(data.get("path"), dict):
                cwd = data["path"].get("cwd") or data["path"].get("root")
            match = match_cwd(cwd, context)
            if not match:
                continue
            git_data = data.get("git") if isinstance(data.get("git"), dict) else {}
            sessions.append(
                session_item(
                    "opencode",
                    path,
                    "opencode-session-json",
                    context,
                    cwd=cwd,
                    match=match,
                    session_id=str(data.get("id") or data.get("sessionID") or path.stem),
                    branch=data.get("branch") or data.get("gitBranch") or data.get("git_branch") or git_data.get("branch"),
                    commit=(
                        data.get("commit_hash")
                        or data.get("commit")
                        or data.get("git_commit")
                        or git_data.get("commit_hash")
                    ),
                )
            )
    return sessions


def cursor_slugs(path: Path) -> set[str]:
    normalized = str(lexical_path(path))
    # Cursor builds have used both a leading-separator-preserving slug and a
    # stripped slug.  Probe both without depending on a version number.
    return {
        normalized.replace(os.sep, "-"),
        normalized.strip(os.sep).replace(os.sep, "-"),
    }


def discover_cursor(context: dict[str, Any], since: float | None) -> list[dict[str, Any]]:
    home = env_path("CODEWISE_CURSOR_HOME", "~/.cursor")
    projects = home / "projects"
    sessions: list[dict[str, Any]] = []
    if not projects.is_dir():
        return sessions
    seen: set[Path] = set()
    for wt in context["worktrees"]:
        for start_root in (Path(wt["root"]), Path(wt["scope_root"])):
            for slug in cursor_slugs(start_root):
                project_dir = projects / slug
                if not project_dir.is_dir():
                    continue
                for pattern in ("agent-transcripts/**/*.txt", "agent-transcripts/**/*.jsonl", "chats/**/*.json"):
                    for path in project_dir.glob(pattern):
                        if path in seen or not path.is_file() or not after_since(path, since):
                            continue
                        seen.add(path)
                        match = match_cwd(str(start_root), context)
                        sessions.append(
                            session_item(
                                "cursor",
                                path,
                                "cursor-transcript",
                                context,
                                cwd=str(start_root),
                                match=match,
                                session_id=path.stem,
                                needs_content_check=True,
                            )
                        )
    return sessions


def unparsed_source_warnings(provider_set: set[str]) -> list[str]:
    warnings: list[str] = []
    if "cursor" in provider_set:
        configured = os.environ.get("CODEWISE_CURSOR_DB")
        cursor_databases = [
            lexical_path(configured)
            if configured
            else Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb",
        ]
        cursor_databases.extend(
            (Path.home() / "Library/Application Support/Cursor/User/workspaceStorage").glob("*/state.vscdb")
        )
        if any(path.is_file() for path in cursor_databases):
            warnings.append(
                "cursor: detected SQLite/KV chat history that is not automatically parsed; "
                "export relevant sessions as JSON or Markdown and add them with --source"
            )

    windsurf = Path.home() / ".codeium/windsurf/cascade"
    if windsurf.exists():
        warnings.append(
            "windsurf: detected opaque protobuf session storage; export relevant sessions "
            "as JSON or Markdown and add them with --source"
        )
    return warnings


def discover_aider(context: dict[str, Any], since: float | None) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for wt in context["worktrees"]:
        for start_root in (Path(wt["root"]), Path(wt["scope_root"])):
            path = start_root / ".aider.chat.history.md"
            if path in seen or not path.is_file() or not after_since(path, since):
                continue
            seen.add(path)
            sessions.append(
                session_item(
                    "aider",
                    path,
                    "aider-markdown",
                    context,
                    cwd=str(start_root),
                    match=match_cwd(str(start_root), context),
                    session_id=f"aider:{path}",
                )
            )
    return sessions


def discover_custom(
    specs: list[str], context: dict[str, Any], since: float | None
) -> tuple[list[dict[str, Any]], list[str]]:
    sessions: list[dict[str, Any]] = []
    warnings: list[str] = []
    for spec in specs:
        provider, separator, pattern = spec.partition("=")
        if not separator or not provider or not pattern:
            warnings.append(f"ignored invalid --source value: {spec!r}; expected name=glob")
            continue
        expanded = os.path.expanduser(pattern)
        for value in sorted(glob.glob(expanded, recursive=True)):
            path = Path(value)
            if not path.is_file() or not after_since(path, since):
                continue
            sessions.append(
                session_item(
                    provider,
                    path,
                    "custom",
                    context,
                    session_id=path.stem,
                    needs_content_check=True,
                )
            )
    return sessions, warnings


def discover(
    root: Path,
    since_value: str | None = None,
    baseline_value: str | None = None,
    providers: Iterable[str] = BUILTIN_PROVIDERS,
    custom_sources: list[str] | None = None,
    known_worktrees: Iterable[str] | None = None,
    include_derived: bool = False,
) -> dict[str, Any]:
    since = parse_since(since_value)
    context, context_warnings = project_context(root, baseline_value, known_worktrees)
    provider_set = set(providers)
    sessions: list[dict[str, Any]] = []
    warnings: list[str] = list(context_warnings)

    discoverers = {
        "claude": discover_claude,
        "codex": discover_codex,
        "gemini": discover_gemini,
        "opencode": discover_opencode,
        "cursor": discover_cursor,
        "aider": discover_aider,
    }
    for provider in BUILTIN_PROVIDERS:
        if provider not in provider_set:
            continue
        if provider == "claude":
            sessions.extend(discover_claude(context, since, include_derived))
        else:
            sessions.extend(discoverers[provider](context, since))

    custom, custom_warnings = discover_custom(custom_sources or [], context, since)
    sessions.extend(custom)
    warnings.extend(custom_warnings)
    warnings.extend(unparsed_source_warnings(provider_set))

    sessions.sort(key=lambda item: (item["mtime"], item["provider"], item["path"], item.get("selector", "")))
    counts: dict[str, int] = {}
    for item in sessions:
        counts[item["provider"]] = counts.get(item["provider"], 0) + 1

    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since_value,
        "baseline": baseline_value,
        "project": context,
        "scanned_providers": [provider for provider in BUILTIN_PROVIDERS if provider in provider_set],
        "providers": counts,
        "session_count": len(sessions),
        "sessions": sessions,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="Codewise scope root")
    parser.add_argument("--since", help="Only include session files newer than this ISO 8601 time")
    parser.add_argument(
        "--baseline",
        help="Baseline commit used to report a merge-base for each Worktree",
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=BUILTIN_PROVIDERS,
        help="Limit built-in providers; repeat for more than one",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="NAME=GLOB",
        help="Add a file-based provider; recursive ** globs are supported",
    )
    parser.add_argument(
        "--known-worktree",
        action="append",
        default=[],
        metavar="PATH",
        help="Re-admit a Worktree path recorded by an earlier run, even if git no longer lists it",
    )
    parser.add_argument(
        "--known-worktrees-file",
        metavar="FILE",
        help="Read known Worktree paths from a file, one per line (# comments and blanks ignored)",
    )
    parser.add_argument(
        "--include-derived",
        action="store_true",
        help="Also list workflow-produced records (wf_*/agent-*.jsonl, journal.jsonl); "
             "excluded by default because a subagent's findings already reach its parent session",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def read_known_worktrees(args: argparse.Namespace) -> list[str]:
    entries: list[str] = list(args.known_worktree)
    if args.known_worktrees_file:
        path = Path(os.path.expanduser(args.known_worktrees_file))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise argparse.ArgumentTypeError(f"cannot read known worktrees file: {exc}") from exc
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                entries.append(stripped)
    return entries


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = discover(
            Path(args.root),
            since_value=args.since,
            baseline_value=args.baseline,
            providers=args.provider or BUILTIN_PROVIDERS,
            custom_sources=args.source,
            known_worktrees=read_known_worktrees(args),
            include_derived=args.include_derived,
        )
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
