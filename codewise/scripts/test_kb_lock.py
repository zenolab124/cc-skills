#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("kb_lock.py")
SPEC = importlib.util.spec_from_file_location("kb_lock", SCRIPT)
assert SPEC and SPEC.loader
kb_lock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kb_lock)


class KbLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.kb = Path(self.temp.name) / "nested" / "kb"
        self.kb.mkdir(parents=True)

    def tearDown(self) -> None:
        for bootstrap in (False, True):
            try:
                path = kb_lock.lock_path(self.kb, bootstrap)
            except kb_lock.LockError:
                continue
            path.unlink(missing_ok=True)
            kb_lock.breaker_path(path).unlink(missing_ok=True)
        self.temp.cleanup()

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_two_separate_cli_processes_cannot_overlap(self) -> None:
        first = self.cli("acquire", str(self.kb), "--branch", "main")
        self.assertEqual(first.returncode, 0, first.stderr)
        owner = json.loads(first.stdout)

        second = self.cli("acquire", str(self.kb), "--branch", "feature")
        self.assertEqual(second.returncode, 2)
        self.assertIn("locked", second.stderr)

        wrong = self.cli("release", str(self.kb), "--token", "wrong")
        self.assertEqual(wrong.returncode, 2)
        released = self.cli("release", str(self.kb), "--token", owner["token"])
        self.assertEqual(released.returncode, 0, released.stderr)

    def test_bootstrap_lock_works_while_kb_and_parents_are_missing(self) -> None:
        missing = Path(self.temp.name) / "new-parent" / "docs" / "knowledge"
        first = self.cli("acquire", str(missing), "--bootstrap")
        self.assertEqual(first.returncode, 0, first.stderr)
        owner = json.loads(first.stdout)

        missing.parent.mkdir(parents=True)
        missing.mkdir()
        second = self.cli("acquire", str(missing), "--bootstrap")
        self.assertEqual(second.returncode, 2)
        released = self.cli(
            "release", str(missing), "--bootstrap", "--token", owner["token"]
        )
        self.assertEqual(released.returncode, 0, released.stderr)

    def test_stale_lock_requires_timeout_and_exact_token(self) -> None:
        owner = kb_lock.acquire(self.kb, "main", 7200)
        lock = self.kb / ".lock"
        old = time.time() - 10
        os.utime(lock, (old, old), follow_symlinks=False)
        with self.assertRaisesRegex(kb_lock.LockError, "does not match"):
            kb_lock.clear_stale(self.kb, "wrong", 1)
        result = kb_lock.clear_stale(self.kb, owner["token"], 1)
        self.assertEqual(result["cleared_token"], owner["token"])
        self.assertFalse(lock.exists())

    def test_lock_symlink_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside-lock"
        (self.kb / ".lock").symlink_to(outside)
        with self.assertRaisesRegex(kb_lock.LockError, "symlink"):
            kb_lock.acquire(self.kb, "main", 7200)

    def test_fifo_lock_is_rejected_without_blocking(self) -> None:
        os.mkfifo(self.kb / ".lock")
        with self.assertRaisesRegex(kb_lock.LockError, "regular file"):
            kb_lock.acquire(self.kb, "main", 7200)

    def test_release_acquire_and_stale_cleanup_are_serialized(self) -> None:
        old_owner = kb_lock.acquire(self.kb, "old", 7200)
        lock = self.kb / ".lock"
        old = time.time() - 10
        os.utime(lock, (old, old), follow_symlinks=False)
        started = threading.Barrier(3)
        outcomes: list[tuple[str, object]] = []

        def cleaner() -> None:
            started.wait()
            try:
                outcomes.append(("clean", kb_lock.clear_stale(self.kb, old_owner["token"], 1)))
            except kb_lock.LockError as exc:
                outcomes.append(("clean-error", str(exc)))

        def replace_owner() -> None:
            started.wait()
            try:
                kb_lock.release(self.kb, old_owner["token"])
                outcomes.append(("release", True))
            except kb_lock.LockError as exc:
                outcomes.append(("release-error", str(exc)))
            try:
                outcomes.append(("acquire", kb_lock.acquire(self.kb, "new", 7200)))
            except kb_lock.LockError as exc:
                outcomes.append(("acquire-error", str(exc)))

        threads = [threading.Thread(target=cleaner), threading.Thread(target=replace_owner)]
        for thread in threads:
            thread.start()
        started.wait()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

        breaker = kb_lock.breaker_path(lock)
        quarantines = list(self.kb.glob(".lock.stale-*"))
        self.assertFalse(breaker.exists())
        self.assertEqual(quarantines, [])
        if lock.exists():
            current = kb_lock.read_lock(lock)
            self.assertIn(current["token"], {old_owner["token"], *[
                value["token"]
                for name, value in outcomes
                if name == "acquire" and isinstance(value, dict)
            ]})

    def test_recover_cleanup_preserves_live_owner_lock(self) -> None:
        owner = kb_lock.acquire(self.kb, "main", 7200)
        lock = self.kb / ".lock"
        cleanup = "c" * 48
        breaker = kb_lock.breaker_path(lock)
        kb_lock.write_exclusive_json(
            breaker,
            {"token": cleanup, "owner_token": owner["token"], "started_at": "old"},
        )
        old = time.time() - 10
        os.utime(breaker, (old, old), follow_symlinks=False)

        result = kb_lock.recover_cleanup(self.kb, cleanup, 1)

        self.assertTrue(result["preserved_live_lock"])
        self.assertEqual(kb_lock.read_lock(lock)["token"], owner["token"])
        self.assertFalse(breaker.exists())

    def test_recover_cleanup_restores_quarantined_owner_lock(self) -> None:
        owner = kb_lock.acquire(self.kb, "main", 7200)
        lock = self.kb / ".lock"
        cleanup = "d" * 48
        breaker = kb_lock.breaker_path(lock)
        quarantine = lock.with_name(f"{lock.name}.stale-{cleanup}")
        kb_lock.write_exclusive_json(
            breaker,
            {"token": cleanup, "owner_token": owner["token"], "started_at": "old"},
        )
        os.rename(lock, quarantine)
        old = time.time() - 10
        os.utime(breaker, (old, old), follow_symlinks=False)

        result = kb_lock.recover_cleanup(self.kb, cleanup, 1)

        self.assertTrue(result["restored_owner_lock"])
        self.assertEqual(kb_lock.read_lock(lock)["token"], owner["token"])
        self.assertFalse(quarantine.exists())
        self.assertFalse(breaker.exists())

    def test_post_rename_failure_keeps_breaker_and_owner_recoverable(self) -> None:
        owner = kb_lock.acquire(self.kb, "main", 7200)
        lock = self.kb / ".lock"
        old = time.time() - 10
        os.utime(lock, (old, old), follow_symlinks=False)
        real_read = kb_lock.read_lock
        reads = 0

        def fail_second_read(path: Path) -> dict:
            nonlocal reads
            reads += 1
            if reads == 2:
                raise kb_lock.LockError("simulated quarantine read failure")
            return real_read(path)

        with mock.patch.object(kb_lock, "read_lock", side_effect=fail_second_read):
            with self.assertRaisesRegex(kb_lock.LockError, "simulated"):
                kb_lock.clear_stale(self.kb, owner["token"], 1)

        breaker = kb_lock.breaker_path(lock)
        cleanup = kb_lock.read_lock(breaker)["token"]
        quarantine = lock.with_name(f"{lock.name}.stale-{cleanup}")
        self.assertTrue(breaker.exists())
        self.assertTrue(quarantine.exists())
        with self.assertRaisesRegex(kb_lock.LockError, "cleanup"):
            kb_lock.acquire(self.kb, "other", 7200)

        os.utime(breaker, (old, old), follow_symlinks=False)
        kb_lock.recover_cleanup(self.kb, cleanup, 1)
        self.assertEqual(kb_lock.read_lock(lock)["token"], owner["token"])


if __name__ == "__main__":
    unittest.main()
