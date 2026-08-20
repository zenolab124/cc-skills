#!/usr/bin/env python3
"""Atomically manage Codewise KB and bootstrap locks."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import sys
import tempfile
import time


class LockError(RuntimeError):
    pass


MAX_LOCK_BYTES = 64 * 1024


def lexical_absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def lock_path(kb: Path, bootstrap: bool = False) -> Path:
    if not bootstrap:
        if not kb.is_dir() or kb.is_symlink():
            raise LockError("KB does not exist as a real directory; use --bootstrap first")
        return kb / ".lock"

    # Bootstrap must serialize before KB or even its parent exists. A stable
    # per-machine temp path keeps release deterministic after atomic rename or
    # creation of formerly missing parent directories.
    ancestor = lexical_absolute(tempfile.gettempdir())
    if not ancestor.is_dir() or ancestor.is_symlink():
        raise LockError("no safe temporary directory for bootstrap lock")
    key = hashlib.sha256(str(kb).encode("utf-8")).hexdigest()[:20]
    return ancestor / f".codewise-bootstrap-{os.getuid()}-{key}.lock"


def breaker_path(lock: Path) -> Path:
    return lock.with_name(f"{lock.name}.break")


def operation_mutex_path(lock: Path) -> Path:
    root = lexical_absolute(tempfile.gettempdir())
    key = hashlib.sha256(str(lock).encode("utf-8")).hexdigest()[:24]
    return root / f".codewise-lock-op-{os.getuid()}-{key}"


@contextmanager
def operation_mutex(lock: Path):
    """Serialize every metadata transition for one logical lock path."""
    path = operation_mutex_path(lock)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise LockError("lock operation mutex must be a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError as exc:
        raise LockError(f"cannot acquire lock operation mutex: {exc}") from exc
    finally:
        if "descriptor" in locals():
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def write_exclusive_json(path: Path, payload: dict) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise
    except OSError as exc:
        raise LockError(f"cannot create guard {path}: {exc}") from exc
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise LockError("short write while creating lock")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_lock(path: Path) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise LockError("lock path must be a regular file")
        if metadata.st_size > MAX_LOCK_BYTES:
            raise LockError("lock file is too large")
        chunks: list[bytes] = []
        remaining = MAX_LOCK_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_LOCK_BYTES:
            raise LockError("lock file is too large")
        value = json.loads(raw.decode("utf-8"))
    except LockError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LockError(f"cannot inspect existing lock: {exc}") from exc
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    if not isinstance(value, dict) or not isinstance(value.get("token"), str):
        raise LockError("existing lock has no valid owner token")
    return value


def age_seconds(path: Path) -> float:
    try:
        return max(0.0, time.time() - path.lstat().st_mtime)
    except OSError as exc:
        raise LockError(f"cannot inspect lock age: {exc}") from exc


def _unlink_owned(lock: Path, token: str) -> None:
    value = read_lock(lock)
    if value.get("token") != token:
        raise LockError("refusing to remove a lock owned by another token")
    try:
        lock.unlink()
    except OSError as exc:
        raise LockError(f"cannot remove lock: {exc}") from exc


def acquire(kb: Path, branch: str, timeout_seconds: int, bootstrap: bool = False) -> dict:
    del timeout_seconds  # Existing locks are never stolen implicitly.
    kb = lexical_absolute(kb)
    lock = lock_path(kb, bootstrap)
    breaker = breaker_path(lock)
    payload = {
        "token": secrets.token_hex(24),
        "hostname": socket.gethostname(),
        "branch": branch,
        "bootstrap": bootstrap,
        "helper_pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "lock_path": str(lock),
    }
    with operation_mutex(lock):
        if lock.is_symlink() or breaker.is_symlink():
            raise LockError("lock paths must not be symlinks")
        if breaker.exists():
            raise LockError("a stale-lock cleanup is in progress")
        try:
            write_exclusive_json(lock, payload)
        except FileExistsError:
            owner = read_lock(lock)
            owner["age_seconds"] = round(age_seconds(lock), 3)
            raise LockError(
                f"KB is locked by {owner}; an expired lock still requires explicit clear-stale"
            )
    return payload


def release(kb: Path, token: str, bootstrap: bool = False) -> None:
    lock = lock_path(lexical_absolute(kb), bootstrap)
    with operation_mutex(lock):
        if breaker_path(lock).exists():
            raise LockError("stale-lock cleanup is in progress; retry release")
        _unlink_owned(lock, token)


def clear_stale(
    kb: Path,
    token: str,
    timeout_seconds: int,
    bootstrap: bool = False,
) -> dict:
    if timeout_seconds < 1:
        raise LockError("timeout must be at least one second")
    lock = lock_path(lexical_absolute(kb), bootstrap)
    breaker = breaker_path(lock)
    breaker_token = secrets.token_hex(24)
    breaker_payload = {
        "token": breaker_token,
        "owner_token": token,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    with operation_mutex(lock):
        try:
            write_exclusive_json(breaker, breaker_payload)
        except FileExistsError as exc:
            raise LockError("another stale-lock cleanup is in progress") from exc

        quarantine = lock.with_name(f"{lock.name}.stale-{breaker_token}")
        lock_moved = False
        try:
            value = read_lock(lock)
            if value.get("token") != token:
                raise LockError("stale-lock token does not match the current owner")
            age = age_seconds(lock)
            if age <= timeout_seconds:
                raise LockError(
                    f"lock is only {age:.1f}s old; timeout is {timeout_seconds}s"
                )
            os.rename(lock, quarantine)
            lock_moved = True
            moved = read_lock(quarantine)
            if moved.get("token") != token:
                raise LockError("lock changed during stale cleanup")
            quarantine.unlink()
            lock_moved = False
            return {"cleared_token": token, "age_seconds": round(age, 3)}
        except FileNotFoundError as exc:
            raise LockError("lock disappeared during stale cleanup") from exc
        except OSError as exc:
            raise LockError(f"cannot clear stale lock: {exc}") from exc
        finally:
            # Once the owner lock has moved, the breaker is the only thing stopping
            # a new acquire. Preserve it across every post-rename failure so
            # recover-cleanup can atomically restore the quarantined owner lock.
            if not lock_moved and not os.path.lexists(quarantine):
                try:
                    breaker.unlink()
                except FileNotFoundError:
                    pass


def recover_cleanup(
    kb: Path,
    cleanup_token: str,
    timeout_seconds: int,
    bootstrap: bool = False,
) -> dict:
    """Recover a crashed stale-lock cleanup without discarding an owner lock."""
    if timeout_seconds < 1:
        raise LockError("timeout must be at least one second")
    lock = lock_path(lexical_absolute(kb), bootstrap)
    breaker = breaker_path(lock)
    with operation_mutex(lock):
        value = read_lock(breaker)
        if value.get("token") != cleanup_token:
            raise LockError("cleanup token does not match the active cleanup guard")
        age = age_seconds(breaker)
        if age <= timeout_seconds:
            raise LockError(
                f"cleanup guard is only {age:.1f}s old; timeout is {timeout_seconds}s"
            )
        quarantine = lock.with_name(f"{lock.name}.stale-{cleanup_token}")
        lock_exists = os.path.lexists(lock)
        quarantine_exists = os.path.lexists(quarantine)
        if lock_exists and quarantine_exists:
            raise LockError("both live and quarantined locks exist; manual inspection required")
        if quarantine_exists:
            quarantined = read_lock(quarantine)
            if quarantined.get("token") != value.get("owner_token"):
                raise LockError("quarantined lock owner does not match cleanup record")
            os.rename(quarantine, lock)
        breaker.unlink()
        return {
            "recovered_cleanup_token": cleanup_token,
            "restored_owner_lock": quarantine_exists,
            "preserved_live_lock": lock_exists,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("acquire", "release", "clear-stale", "recover-cleanup")
    )
    parser.add_argument("kb")
    parser.add_argument("--branch", default="unknown")
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--token", help="Opaque token returned by acquire")
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    kb = lexical_absolute(args.kb)
    try:
        if args.action == "acquire":
            result = acquire(kb, args.branch, args.timeout_seconds, args.bootstrap)
        elif args.action == "release":
            if not args.token:
                parser.error("--token is required for release")
            release(kb, args.token, args.bootstrap)
            result = {"released": True}
        elif args.action == "clear-stale":
            if not args.token:
                parser.error("--token is required for clear-stale")
            result = clear_stale(
                kb,
                args.token,
                args.timeout_seconds,
                args.bootstrap,
            )
        else:
            if not args.token:
                parser.error("--token is required for recover-cleanup")
            result = recover_cleanup(
                kb,
                args.token,
                args.timeout_seconds,
                args.bootstrap,
            )
        json.dump(result, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    except LockError as exc:
        print(f"lock error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
