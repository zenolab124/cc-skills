#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("kb_tree_guard.py")
SPEC = importlib.util.spec_from_file_location("kb_tree_guard", SCRIPT)
assert SPEC and SPEC.loader
kb_tree_guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kb_tree_guard)


class KbTreeGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.scope = Path(self.temp.name) / "project"
        self.kb = self.scope / "docs" / "knowledge"
        (self.kb / "domains").mkdir(parents=True)
        (self.kb / "domains" / "x.md").write_text("x\n", encoding="utf-8")
        (self.kb / ".git").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_clean_tree_passes(self) -> None:
        result = kb_tree_guard.guard(self.kb, self.scope)
        self.assertEqual(result["hard_stops"], [])

    def test_control_file_symlink_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        (self.kb / "_sync.json").symlink_to(outside)

        with self.assertRaisesRegex(kb_tree_guard.GuardError, "symlink"):
            kb_tree_guard.guard(self.kb, self.scope)

    def test_nested_entry_symlink_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (self.kb / "domains" / "escape.md").symlink_to(outside)

        with self.assertRaisesRegex(kb_tree_guard.GuardError, "symlink"):
            kb_tree_guard.guard(self.kb, self.scope)

    def test_archive_symlink_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside-archive"
        outside.mkdir()
        archive = self.kb / ".archive"
        archive.mkdir()
        (archive / "escape").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(kb_tree_guard.GuardError, "symlink"):
            kb_tree_guard.guard(self.kb, self.scope)

    def test_registry_symlink_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside-agents.md"
        outside.write_text("outside\n", encoding="utf-8")
        (self.scope / "AGENTS.md").symlink_to(outside)

        with self.assertRaisesRegex(kb_tree_guard.GuardError, "registry"):
            kb_tree_guard.guard(self.kb, self.scope)

    def test_control_directory_is_rejected(self) -> None:
        (self.kb / "_sync.json").mkdir()
        with self.assertRaisesRegex(kb_tree_guard.GuardError, "regular file"):
            kb_tree_guard.guard(self.kb, self.scope)

    def test_gitfile_is_rejected(self) -> None:
        (self.kb / ".git").rmdir()
        (self.kb / ".git").write_text("gitdir: /outside\n", encoding="utf-8")
        with self.assertRaisesRegex(kb_tree_guard.GuardError, "real directory"):
            kb_tree_guard.guard(self.kb, self.scope)

    def test_control_fifo_is_rejected(self) -> None:
        fifo = self.kb / "_sync.json"
        import os

        os.mkfifo(fifo)
        with self.assertRaisesRegex(kb_tree_guard.GuardError, "regular file"):
            kb_tree_guard.guard(self.kb, self.scope)


if __name__ == "__main__":
    unittest.main()
