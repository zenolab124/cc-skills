#!/usr/bin/env python3
"""Exercise navigation with real repositories and worktrees, without agent calls."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import session_start


class SessionStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "repo with spaces"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "initial")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True)

    def write(self, relative, content="sentinel-body-not-for-context", root=None):
        p = (root or self.root) / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def context(self, cwd=None):
        result = session_start.message(cwd or self.root)
        raw = json.dumps(result, ensure_ascii=False)
        self.assertLessEqual(len(raw.encode()), session_start.MAX_OUTPUT_BYTES)
        self.assertNotIn("sentinel-body-not-for-context", raw)
        return result.get("hookSpecificOutput", {}).get("additionalContext", "")

    def test_no_project_knowledge_is_silent(self):
        self.assertEqual(session_start.message(self.root), {})

    def test_large_index_and_corrupt_archive_are_not_read_or_written(self):
        self.write("docs/knowledge/INDEX.md", "sentinel-body-not-for-context" * 100000)
        self.write("docs/knowledge/.archive/old/_sync.json", "invalid json")
        state = self.write("docs/knowledge/_sync.json", '{"baseline_commit":"unchanged"}')
        self.write("docs/PROJECT_GUIDE.md")
        before = state.read_bytes()
        # Guard against adding whole-document reads later.
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read bodies")):
            context = self.context()
        self.assertIn(str(self.root / "docs/PROJECT_GUIDE.md"), context)
        self.assertEqual(state.read_bytes(), before)

    def test_subdirectory_resolves_root_entry(self):
        self.write("docs/PROJECT_GUIDE.md")
        child = self.root / "src/nested"
        child.mkdir(parents=True)
        self.assertIn(str(self.root / "docs/PROJECT_GUIDE.md"), self.context(child))

    def test_nearest_nested_scope_wins(self):
        self.write("docs/PROJECT_GUIDE.md")
        self.write("apps/client/docs/PROJECT_GUIDE.md")
        child = self.root / "apps/client/src"
        child.mkdir()
        context = self.context(child)
        self.assertIn(str(self.root / "apps/client/docs/PROJECT_GUIDE.md"), context)
        self.assertNotIn('"' + str(self.root / "docs/PROJECT_GUIDE.md") + '"', context)

    def test_missing_knowledge_does_not_bootstrap(self):
        self.write(".codewise-bootstrap.json", "invalid hint must not be parsed here")
        self.assertTrue(self.context())
        self.assertFalse((self.root / "docs/knowledge").exists())

    def test_linked_worktree_uses_matching_main_scope_with_qualification(self):
        self.write("apps/client/docs/knowledge/INDEX.md")
        self.write("apps/client/.codewise-bootstrap.json")
        self.git("add", "apps/client/.codewise-bootstrap.json")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "scope")
        linked = self.base / "linked"
        self.git("worktree", "add", "-qb", "feature", str(linked))
        child = linked / "apps/client/src"
        child.mkdir()
        context = self.context(child)
        self.assertIn(str(self.root / "apps/client/docs/knowledge/INDEX.md"), context)
        self.assertIn("父分支视角", context)
        self.assertNotIn(str(linked / "docs/knowledge"), context)

    def test_linked_local_guide_precedes_parent_guide(self):
        self.write("docs/PROJECT_GUIDE.md")
        linked = self.base / "linked"
        self.git("worktree", "add", "-qb", "feature", str(linked))
        self.write("docs/PROJECT_GUIDE.md", root=linked)
        context = self.context(linked)
        self.assertIn(str(linked / "docs/PROJECT_GUIDE.md"), context)
        self.assertNotIn(str(self.root / "docs/PROJECT_GUIDE.md"), context)

    def test_symlink_entry_is_not_advertised(self):
        outside = self.write("outside.md", root=self.base)
        self.write(".codewise-bootstrap.json")
        guide = self.root / "docs/PROJECT_GUIDE.md"
        guide.parent.mkdir()
        guide.symlink_to(outside)
        self.assertNotIn(str(guide), self.context())

    def test_non_git_scope(self):
        other = self.base / "plain"
        self.write("docs/PROJECT_GUIDE.md", root=other)
        self.assertIn(str(other / "docs/PROJECT_GUIDE.md"), self.context(other))

    def test_host_cwd_overrides_shell_and_other_host_environment(self):
        self.write("docs/PROJECT_GUIDE.md")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.base))
        result = subprocess.run(
            [sys.executable, str(Path(session_start.__file__))],
            input=json.dumps({"cwd": str(self.root)}), capture_output=True, text=True,
            cwd=self.base, env=env, check=True,
        )
        self.assertIn(str(self.root / "docs/PROJECT_GUIDE.md"), json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_invalid_input_and_missing_git_are_nonblocking(self):
        self.write("docs/PROJECT_GUIDE.md")
        result = subprocess.run(
            [sys.executable, str(Path(session_start.__file__))], input="invalid json",
            capture_output=True, text=True, cwd=self.root,
            env=dict(os.environ, PATH="", CLAUDE_PROJECT_DIR=str(self.root)), check=True,
        )
        self.assertTrue(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_shell_adapter_matches_python_entry(self):
        self.write("docs/PROJECT_GUIDE.md")
        shell = Path(session_start.__file__).with_name("codewise-inject.sh")
        result = subprocess.run(["bash", str(shell), "--cwd", str(self.root)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), session_start.message(self.root))


if __name__ == "__main__":
    unittest.main()
