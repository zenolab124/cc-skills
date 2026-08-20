#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("resolve_context.py")
SPEC = importlib.util.spec_from_file_location("resolve_context", SCRIPT)
assert SPEC and SPEC.loader
resolve_context = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolve_context)


def run(*args: str) -> str:
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


class ResolveContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.repo = base / "project with spaces"
        self.worktree = base / "feature with spaces"
        self.repo.mkdir()
        run("git", "-C", str(self.repo), "init", "-q", "-b", "main")
        run("git", "-C", str(self.repo), "config", "user.email", "codewise@example.invalid")
        run("git", "-C", str(self.repo), "config", "user.name", "Codewise Test")
        (self.repo / ".gitignore").write_text("/apps/web/docs/knowledge/\n", encoding="utf-8")
        (self.repo / "apps" / "web").mkdir(parents=True)
        (self.repo / "apps" / "web" / "package.json").write_text("{}\n", encoding="utf-8")
        (self.repo / "README.md").write_text("test\n", encoding="utf-8")
        run("git", "-C", str(self.repo), "add", ".")
        run("git", "-C", str(self.repo), "commit", "-qm", "initial")
        run("git", "-C", str(self.repo), "worktree", "add", "-qb", "feature", str(self.worktree))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def init_kb(self, scope: str = "apps/web") -> Path:
        kb = self.repo / scope / "docs" / "knowledge"
        kb.mkdir(parents=True)
        run("git", "-C", str(kb), "init", "-q", "-b", "main")
        identity = {
            "root_commits": run(
                "git", "-C", str(self.repo), "rev-list", "--max-parents=0", "HEAD"
            ).splitlines(),
            "scope_root": scope,
        }
        (kb / "_identity.json").write_text(json.dumps(identity), encoding="utf-8")
        return kb

    def test_explicit_subproject_resolves_from_main_and_linked_worktree(self) -> None:
        kb = self.init_kb()
        main = resolve_context.resolve(self.repo / "apps" / "web")
        linked = resolve_context.resolve(self.worktree / "apps" / "web")

        self.assertEqual(main["kb"], str(kb.resolve()))
        self.assertEqual(linked["kb"], str(kb.resolve()))
        self.assertEqual(linked["main_worktree"], str(self.repo.resolve()))
        self.assertTrue(linked["is_linked_worktree"])
        self.assertEqual(linked["hard_stops"], [])

    def test_scope_component_with_trailing_space_is_preserved(self) -> None:
        scope = self.repo / "apps" / "space "
        scope.mkdir()
        (scope / "main.py").write_text("pass\n", encoding="utf-8")
        run("git", "-C", str(self.repo), "add", "apps/space /main.py")
        run("git", "-C", str(self.repo), "commit", "-qm", "space scope")
        (self.repo / ".gitignore").write_text(
            "/apps/web/docs/knowledge/\n/apps/space /docs/knowledge/\n",
            encoding="utf-8",
        )

        result = resolve_context.resolve(scope)

        self.assertEqual(result["scope_root"], "apps/space ")
        self.assertEqual(result["kb"], str(scope.resolve() / "docs" / "knowledge"))
        self.assertNotIn("scope-path-mismatch", result["hard_stops"])

    def test_linked_worktree_cannot_initialize_missing_kb(self) -> None:
        result = resolve_context.resolve(self.worktree / "apps" / "web")
        self.assertIn("linked-worktree-kb-missing", result["hard_stops"])
        self.assertEqual(result["mode"], "blocked")

    def test_dangling_knowledge_symlink_cannot_escape_scope(self) -> None:
        link = self.repo / "apps" / "web" / "docs" / "knowledge"
        link.parent.mkdir(parents=True)
        outside = Path(self.temp.name) / "outside" / "knowledge"
        link.symlink_to(outside)

        result = resolve_context.resolve(self.repo / "apps" / "web")

        expected_link = self.repo.resolve() / "apps" / "web" / "docs" / "knowledge"
        self.assertEqual(result["kb"], str(expected_link))
        self.assertEqual(result["mode"], "blocked")
        self.assertIn("kb-path-is-symlink", result["hard_stops"])
        self.assertIn("kb-resolves-outside-main-worktree", result["hard_stops"])
        self.assertFalse(outside.exists())

    def test_existing_plain_directory_is_rejected(self) -> None:
        kb = self.repo / "apps" / "web" / "docs" / "knowledge"
        kb.mkdir(parents=True)
        result = resolve_context.resolve(self.repo / "apps" / "web")
        self.assertIn("kb-not-independent-repository", result["hard_stops"])

    def test_kb_linked_worktree_is_not_an_independent_repository(self) -> None:
        kb_parent = self.repo / "apps" / "web" / "docs"
        kb_parent.mkdir(parents=True)
        run("git", "-C", str(self.repo), "worktree", "add", "-qb", "kb-linked", str(kb_parent / "knowledge"))

        result = resolve_context.resolve(self.repo / "apps" / "web")

        self.assertIn("kb-not-independent-repository", result["hard_stops"])
        self.assertIn(".git must be a real directory", result["kb_repository_error"])

    def test_same_repository_different_scope_identity_is_rejected(self) -> None:
        kb = self.init_kb()
        (self.repo / ".gitignore").write_text(
            "/apps/web/docs/knowledge/\n/apps/api/docs/knowledge/\n", encoding="utf-8"
        )
        api = self.repo / "apps" / "api"
        api.mkdir(parents=True)
        wrong = api / "docs" / "knowledge"
        wrong.parent.mkdir(parents=True)
        run("cp", "-R", str(kb), str(wrong))
        result = resolve_context.resolve(api)
        self.assertIn("identity-mismatch", result["hard_stops"])

        override = resolve_context.resolve(api, allow_reidentify=True)
        self.assertNotIn("identity-mismatch", override["hard_stops"])
        self.assertEqual(override["identity_override_reasons"], ["identity-mismatch"])

    def test_reidentify_cannot_override_identity_symlink(self) -> None:
        kb = self.init_kb()
        identity = kb / "_identity.json"
        identity.unlink()
        outside = Path(self.temp.name) / "outside-identity.json"
        outside.write_text("{}\n", encoding="utf-8")
        identity.symlink_to(outside)

        result = resolve_context.resolve(
            self.repo / "apps" / "web",
            allow_reidentify=True,
        )

        self.assertEqual(result["mode"], "blocked")
        self.assertIn("identity-path-is-symlink", result["hard_stops"])
        self.assertEqual(result["identity_override_reasons"], [])

    def test_malformed_identity_returns_hard_stop_without_traceback(self) -> None:
        kb = self.init_kb()
        (kb / "_identity.json").write_text(
            json.dumps({"root_commits": [1, "x"], "scope_root": "apps/web"}),
            encoding="utf-8",
        )

        result = resolve_context.resolve(self.repo / "apps" / "web")

        self.assertIn("identity-invalid", result["hard_stops"])
        self.assertIsNone(result["identity"])

    def test_parent_index_tracking_is_hard_stop(self) -> None:
        tracked = self.repo / "apps" / "tracked" / "docs" / "knowledge"
        tracked.mkdir(parents=True)
        (tracked / "INDEX.md").write_text("old\n", encoding="utf-8")
        run("git", "-C", str(self.repo), "add", "-f", "apps/tracked/docs/knowledge/INDEX.md")
        run("git", "-C", str(self.repo), "commit", "-qm", "track old knowledge")
        result = resolve_context.resolve(self.repo / "apps" / "tracked")
        self.assertIn("kb-not-independent-repository", result["hard_stops"])
        self.assertIn("kb-tracked-by-main-repository", result["hard_stops"])

    def test_non_git_plain_knowledge_directory_is_rejected(self) -> None:
        root = Path(self.temp.name) / "plain project"
        (root / "docs" / "knowledge").mkdir(parents=True)

        result = resolve_context.resolve(root)

        self.assertEqual(result["mode"], "blocked")
        self.assertIn("kb-not-independent-repository", result["hard_stops"])

    def test_non_git_identity_uses_weak_path_anchor(self) -> None:
        root = Path(self.temp.name) / "plain anchored project"
        kb = root / "docs" / "knowledge"
        kb.mkdir(parents=True)
        run("git", "-C", str(kb), "init", "-q")
        expected = resolve_context.resolve(root)["expected_identity"]
        (kb / "_identity.json").write_text(json.dumps(expected), encoding="utf-8")

        result = resolve_context.resolve(root)

        self.assertEqual(result["mode"], "existing-non-git")
        self.assertEqual(result["hard_stops"], [])
        self.assertTrue(result["identity_matches"])

    def test_zero_commit_git_scope_is_reported(self) -> None:
        root = Path(self.temp.name) / "empty git"
        root.mkdir()
        run("git", "-C", str(root), "init", "-q")

        result = resolve_context.resolve(root)

        self.assertFalse(result["has_commits"])
        self.assertEqual(result["mode"], "initialize-main")
        self.assertIn("weak_path_sha256", result["expected_identity"])
        self.assertNotIn("root_commits", result["expected_identity"])

    def test_shallow_repository_cannot_create_identity(self) -> None:
        (self.repo / "second.txt").write_text("second\n", encoding="utf-8")
        run("git", "-C", str(self.repo), "add", "second.txt")
        run("git", "-C", str(self.repo), "commit", "-qm", "second")
        shallow = Path(self.temp.name) / "shallow"
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--depth=1",
                "--branch",
                "main",
                f"file://{self.repo}",
                str(shallow),
            ],
            check=True,
        )

        result = resolve_context.resolve(shallow / "apps" / "web")

        self.assertTrue(result["source_repository_shallow"])
        self.assertIn("source-repository-shallow", result["hard_stops"])
        self.assertEqual(result["mode"], "blocked")

    def test_git_status_failure_is_not_reported_as_clean(self) -> None:
        real_git = resolve_context.git

        def fail_status(root: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args and args[0] == "status":
                return subprocess.CompletedProcess(
                    ["git", "status"], 128, stdout="", stderr="simulated failure"
                )
            return real_git(root, *args, **kwargs)

        with mock.patch.object(resolve_context, "git", side_effect=fail_status):
            result = resolve_context.resolve(self.repo / "apps" / "web")

        self.assertIn("source-status-unavailable", result["hard_stops"])
        self.assertEqual(result["source_status_error"], "simulated failure")

    def test_scope_and_worktree_resolution_failures_are_hard_stops(self) -> None:
        real_git = resolve_context.git

        def fail_resolution(root: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args[:2] in (("rev-parse", "--show-prefix"), ("worktree", "list")):
                return subprocess.CompletedProcess(
                    ["git", *args], 128, stdout="", stderr="simulated failure"
                )
            return real_git(root, *args, **kwargs)

        with mock.patch.object(resolve_context, "git", side_effect=fail_resolution):
            result = resolve_context.resolve(self.repo / "apps" / "web")

        self.assertIn("scope-resolution-failed", result["hard_stops"])
        self.assertIn("main-worktree-resolution-failed", result["hard_stops"])

    def test_broken_git_marker_does_not_fall_back_to_non_git(self) -> None:
        broken = Path(self.temp.name) / "broken"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: /definitely/missing\n", encoding="utf-8")

        result = resolve_context.resolve(broken)

        self.assertEqual(result["is_git"], None)
        self.assertIn("source-repository-unavailable", result["hard_stops"])

    def test_source_dirty_is_scoped_to_requested_root(self) -> None:
        (self.repo / "README.md").write_text("outside scope change\n", encoding="utf-8")
        clean_scope = resolve_context.resolve(self.repo / "apps" / "web")
        self.assertFalse(clean_scope["source_dirty"])

        (self.repo / "apps" / "web" / "package.json").write_text(
            '{"dirty": true}\n', encoding="utf-8"
        )
        dirty_scope = resolve_context.resolve(self.repo / "apps" / "web")
        self.assertTrue(dirty_scope["source_dirty"])
        self.assertTrue(any("package.json" in line for line in dirty_scope["source_changes"]))

    def test_cli_can_recheck_clean_source_and_captured_head(self) -> None:
        scope = self.repo / "apps" / "web"
        head = run("git", "-C", str(self.repo), "rev-parse", "HEAD")
        clean = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                str(scope),
                "--require-clean-source",
                "--verify-source-head",
                head,
            ],
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual(clean.returncode, 0)

        (scope / "package.json").write_text('{"changed": true}\n', encoding="utf-8")
        dirty = subprocess.run(
            ["python3", str(SCRIPT), str(scope), "--require-clean-source"],
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual(dirty.returncode, 2)
        self.assertIn("source-scope-dirty", json.loads(dirty.stdout)["hard_stops"])

        changed = subprocess.run(
            ["python3", str(SCRIPT), str(scope), "--verify-source-head", "0" * 40],
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual(changed.returncode, 2)
        self.assertIn("source-head-changed", json.loads(changed.stdout)["hard_stops"])


if __name__ == "__main__":
    unittest.main()
