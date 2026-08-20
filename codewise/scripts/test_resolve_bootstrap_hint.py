#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("resolve_bootstrap_hint.py")
SPEC = importlib.util.spec_from_file_location("resolve_bootstrap_hint", SCRIPT)
assert SPEC and SPEC.loader
resolve_bootstrap_hint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolve_bootstrap_hint)


class ResolveBootstrapHintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        self.kb = self.root / "docs" / "knowledge"
        self.hint = self.root / resolve_bootstrap_hint.HINT_NAME
        self.write_hint()
        subprocess.run(["git", "-C", str(self.root), "add", self.hint.name], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "init"], check=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_hint(self, **overrides: object) -> None:
        value: dict[str, object] = {
            "schema_version": 1,
            "knowledge_path": "docs/knowledge",
            "knowledge_remote": "https://github.com/example/project-knowledge.git",
            "knowledge_branch": "master",
            "source_branch": "main",
        }
        value.update(overrides)
        self.hint.write_text(json.dumps(value), encoding="utf-8")

    def test_valid_tracked_hint(self) -> None:
        result = resolve_bootstrap_hint.load_hint(self.root, self.kb)
        self.assertTrue(result["tracked_at_head"])
        self.assertEqual(result["source_branch"], "main")

    def test_uncommitted_change_is_rejected(self) -> None:
        self.write_hint(source_branch="develop")
        with self.assertRaisesRegex(resolve_bootstrap_hint.HintError, "committed HEAD"):
            resolve_bootstrap_hint.load_hint(self.root, self.kb)

    def test_wrong_knowledge_path_is_rejected(self) -> None:
        self.write_hint(knowledge_path="other/knowledge")
        subprocess.run(["git", "-C", str(self.root), "add", self.hint.name], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "change"], check=True)
        with self.assertRaisesRegex(resolve_bootstrap_hint.HintError, "resolver-selected KB"):
            resolve_bootstrap_hint.load_hint(self.root, self.kb)

    def test_remote_credentials_are_rejected(self) -> None:
        self.write_hint(knowledge_remote="https://user:secret@example.com/project.git")
        subprocess.run(["git", "-C", str(self.root), "add", self.hint.name], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "change"], check=True)
        with self.assertRaisesRegex(resolve_bootstrap_hint.HintError, "credentials"):
            resolve_bootstrap_hint.load_hint(self.root, self.kb)

    def test_unknown_field_is_rejected(self) -> None:
        self.write_hint(extra=True)
        subprocess.run(["git", "-C", str(self.root), "add", self.hint.name], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "change"], check=True)
        with self.assertRaisesRegex(resolve_bootstrap_hint.HintError, "unknown fields"):
            resolve_bootstrap_hint.load_hint(self.root, self.kb)


if __name__ == "__main__":
    unittest.main()
