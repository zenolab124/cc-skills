#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("source_snapshot.py")
SPEC = importlib.util.spec_from_file_location("source_snapshot", SCRIPT)
assert SPEC and SPEC.loader
source_snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_snapshot)


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.kb = self.root / "docs" / "knowledge"
        self.kb.mkdir(parents=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("print('a')\n", encoding="utf-8")
        (self.kb / "INDEX.md").write_text("knowledge\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_kb_and_build_outputs_do_not_change_digest(self) -> None:
        first = source_snapshot.snapshot(self.root, self.kb, include_files=True)
        (self.kb / "INDEX.md").write_text("changed knowledge\n", encoding="utf-8")
        (self.root / "node_modules" / "x").mkdir(parents=True)
        (self.root / "node_modules" / "x" / "index.js").write_text("ignored\n", encoding="utf-8")
        second = source_snapshot.snapshot(self.root, self.kb, include_files=True)

        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(first["files"], ["src/main.py"])

    def test_source_change_is_detected_by_cli(self) -> None:
        first = source_snapshot.snapshot(self.root, self.kb, include_files=True)
        (self.root / "src" / "main.py").write_text("print('b')\n", encoding="utf-8")
        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                str(self.root),
                "--kb",
                str(self.kb),
                "--verify",
                first["digest"],
            ],
            text=True,
            stdout=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("source-snapshot-changed", json.loads(result.stdout)["hard_stops"])

    def test_source_symlink_target_is_hashed_without_following(self) -> None:
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        (self.root / "src" / "link.py").symlink_to(outside)

        first = source_snapshot.snapshot(self.root, self.kb, include_files=True)
        outside.write_text("changed outside\n", encoding="utf-8")
        second = source_snapshot.snapshot(self.root, self.kb, include_files=True)

        self.assertEqual(first["digest"], second["digest"])
        self.assertIn("src/link.py", first["files"])

    def test_git_scope_uses_project_owned_nonignored_files(self) -> None:
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        (self.root / ".gitignore").write_text(".env\n", encoding="utf-8")
        (self.root / ".env").write_text("secret one\n", encoding="utf-8")
        first = source_snapshot.snapshot(self.root, self.kb, include_files=True)
        (self.root / ".env").write_text("secret two\n", encoding="utf-8")
        second = source_snapshot.snapshot(self.root, self.kb, include_files=True)

        self.assertEqual(first["digest"], second["digest"])
        self.assertNotIn(".env", first["files"])

    def test_cli_omits_file_list_by_default(self) -> None:
        result = subprocess.run(
            ["python3", str(SCRIPT), str(self.root), "--kb", str(self.kb)],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        payload = json.loads(result.stdout)
        self.assertNotIn("files", payload)
        self.assertEqual(payload["file_count"], 1)

    def test_git_subproject_scope_does_not_include_repository_siblings(self) -> None:
        repo = Path(self.temp.name) / "monorepo"
        scope = repo / "apps" / "web"
        kb = scope / "docs" / "knowledge"
        kb.mkdir(parents=True)
        (scope / "main.ts").write_text("web\n", encoding="utf-8")
        (repo / "outside.ts").write_text("outside\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

        result = source_snapshot.snapshot(scope, kb, include_files=True)

        self.assertEqual(result["files"], ["main.ts"])

    def test_git_initialization_or_first_commit_changes_digest(self) -> None:
        plain = source_snapshot.snapshot(self.root, self.kb)
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        unborn = source_snapshot.snapshot(self.root, self.kb)
        self.assertNotEqual(plain["digest"], unborn["digest"])

        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.name", "Codewise Test"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "first"], check=True)
        committed = source_snapshot.snapshot(self.root, self.kb)
        self.assertNotEqual(unborn["digest"], committed["digest"])
        self.assertTrue(committed["source_state"]["has_commits"])


if __name__ == "__main__":
    unittest.main()
