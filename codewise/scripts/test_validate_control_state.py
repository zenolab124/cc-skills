#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("validate_control_state.py")
SPEC = importlib.util.spec_from_file_location("validate_control_state", SCRIPT)
assert SPEC and SPEC.loader
control_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control_state)


class ControlStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.kb = self.base / "kb"
        self.source.mkdir()
        self.kb.mkdir()
        self._git_init(self.source)
        (self.source / "tracked.txt").write_text("source\n", encoding="utf-8")
        self.source_commit = self._commit_all(self.source, "source")

        self._git_init(self.kb)
        self.write_json(
            self.kb / "_meta.json",
            {"anchor_kind": "main", "branch": "main"},
        )
        self.write_json(self.kb / "_branches.json", {})
        self.write_json(self.kb / "_sync.json", self.sync())
        self.kb_commit = self._commit_all(self.kb, "initial knowledge")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _git_init(self, path: Path) -> None:
        subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "config", "user.name", "Codewise Test"],
            check=True,
        )

    def _commit_all(self, path: Path, message: str) -> str:
        subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(path), "commit", "-qm", message], check=True)
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

    def write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def sync(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "codewise_version": 3,
            "baseline_commit": self.source_commit,
            "synced_at": "2026-08-13T10:00:00+08:00",
            "scope_root": ".",
            "multi_codetree": ["src"],
            "session_sources": {"codex": 1},
            "worktree_count": 1,
            "known_worktrees": ["."],
        }
        value.update(overrides)
        return value

    def add_independent(
        self,
        branch: str = "feat/long",
        slug: str = "feat-long",
        *,
        meta_branch: str | None = None,
        forked_from: str | None = None,
        sync: dict[str, object] | None = None,
    ) -> Path:
        branches = json.loads((self.kb / "_branches.json").read_text(encoding="utf-8"))
        branches[branch] = {
            "mode": "independent",
            "parent_anchor": "main",
            "slug": slug,
            "confirmed_at": "2026-08-13T10:05:00+08:00",
        }
        self.write_json(self.kb / "_branches.json", branches)
        root = self.kb / ".branches" / slug
        root.mkdir(parents=True)
        self.write_json(
            root / "_meta.json",
            {
                "anchor_kind": "branch",
                "branch": meta_branch if meta_branch is not None else branch,
                "forked_from": self.kb_commit if forked_from is None else forked_from,
            },
        )
        self.write_json(root / "_sync.json", sync if sync is not None else self.sync())
        return root

    def archive_branch(
        self,
        branch: str = "feat/long",
        slug: str = "feat-long",
        archive_name: str = "feat-long-20260813T100000",
        *,
        outcome: str = "merged",
        meta_outcome: str | None = None,
    ) -> Path:
        active = self.add_independent(branch, slug)
        archive = self.kb / ".archive" / archive_name
        archive.parent.mkdir()
        active.rename(archive)
        meta = json.loads((archive / "_meta.json").read_text(encoding="utf-8"))
        meta["outcome"] = meta_outcome if meta_outcome is not None else outcome
        self.write_json(archive / "_meta.json", meta)
        self.write_json(
            self.kb / "_branches.json",
            {
                branch: {
                    "mode": "archived",
                    "parent_anchor": "main",
                    "slug": slug,
                    "confirmed_at": "2026-08-13T10:05:00+08:00",
                    "archive_path": f".archive/{archive_name}",
                    "archived_at": "2026-08-13T10:10:00+08:00",
                    "outcome": outcome,
                }
            },
        )
        return archive

    def validate(self, kbr: Path | None = None) -> dict[str, object]:
        return control_state.validate_control_state(self.kb, kbr, self.source)

    def test_valid_root_and_branch_emit_verified_normalized_commits(self) -> None:
        branch_root = self.add_independent()

        result = self.validate(branch_root)

        self.assertEqual(result["selected"]["branch"], "feat/long")
        self.assertEqual(result["root"]["normalized"]["baseline_commit"], self.source_commit)
        branch = result["branches"]["feat/long"]
        self.assertEqual(branch["normalized"]["forked_from"], self.kb_commit)
        self.assertEqual(branch["normalized"]["baseline_commit"], self.source_commit)

    def test_cli_requires_source_root_for_non_null_baseline_without_traceback(self) -> None:
        result = subprocess.run(
            ["python3", str(SCRIPT), "--kb", str(self.kb)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("needs --source-root", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_null_commits_do_not_require_git_repositories(self) -> None:
        plain = self.base / "plain-kb"
        plain.mkdir()
        self.write_json(plain / "_meta.json", {"anchor_kind": "main", "branch": "main"})
        self.write_json(plain / "_branches.json", {})
        self.write_json(plain / "_sync.json", self.sync(baseline_commit=None))

        result = control_state.validate_control_state(plain)

        self.assertEqual(result["selected"]["kind"], "main")

    def test_non_git_control_state_has_no_fake_branch(self) -> None:
        plain = self.base / "non-git-kb"
        plain.mkdir()
        self.write_json(
            plain / "_meta.json",
            {"anchor_kind": "non-git", "degraded_acknowledged": True},
        )
        self.write_json(plain / "_branches.json", {})
        self.write_json(plain / "_sync.json", self.sync(baseline_commit=None))

        result = control_state.validate_control_state(plain)

        self.assertEqual(result["selected"]["kind"], "non-git")
        self.assertIsNone(result["selected"]["branch"])

    def test_git_sync_scope_must_match_measured_source_scope(self) -> None:
        repo = self.base / "monorepo"
        scope = repo / "apps" / "web"
        scope.mkdir(parents=True)
        self._git_init(repo)
        (scope / "main.py").write_text("pass\n", encoding="utf-8")
        commit = self._commit_all(repo, "scope")
        self.write_json(
            self.kb / "_sync.json",
            self.sync(baseline_commit=commit, scope_root="apps/api"),
        )

        with self.assertRaisesRegex(control_state.ValidationError, "measured"):
            control_state.validate_control_state(self.kb, source_root_value=scope)

    def test_archived_missing_source_objects_are_provenance_not_hard_stop(self) -> None:
        archive = self.archive_branch(outcome="abandoned")
        missing = "f" * 40
        meta = json.loads((archive / "_meta.json").read_text(encoding="utf-8"))
        meta["forked_from"] = missing
        self.write_json(archive / "_meta.json", meta)
        self.write_json(archive / "_sync.json", self.sync(baseline_commit=missing))

        result = self.validate()

        self.assertEqual(
            result["branches"]["feat/long"]["normalized"]["baseline_commit"], missing
        )

    def test_branch_keys_are_checked_by_git(self) -> None:
        for branch in ("bad..branch", "-option", "bad branch", "bad\nbranch"):
            with self.subTest(branch=branch):
                self.write_json(
                    self.kb / "_branches.json",
                    {
                        branch: {
                            "mode": "inherit",
                            "parent_anchor": "main",
                            "confirmed_at": "2026-08-13T10:00:00+08:00",
                        }
                    },
                )
                with self.assertRaises(control_state.ValidationError):
                    self.validate()

    def test_mode_and_registration_fields_fail_closed(self) -> None:
        invalid_values = (
            {"mode": "copy", "parent_anchor": "main", "confirmed_at": "2026-08-13T10:00:00Z"},
            {"mode": "inherit", "parent_anchor": "main", "confirmed_at": "2026-08-13T10:00:00Z", "slug": "feat-x"},
            {"mode": "inherit", "parent_anchor": "other", "confirmed_at": "2026-08-13T10:00:00Z"},
            {"mode": "inherit", "parent_anchor": "main", "confirmed_at": "2026-08-13T10:00:00"},
        )
        for registration in invalid_values:
            with self.subTest(registration=registration):
                self.write_json(self.kb / "_branches.json", {"feat/x": registration})
                with self.assertRaises(control_state.ValidationError):
                    self.validate()

    def test_independent_slug_must_be_canonical_safe_and_unique(self) -> None:
        for slug in ("feat/x", "../feat-x", "wrong", "."):
            with self.subTest(slug=slug):
                self.write_json(
                    self.kb / "_branches.json",
                    {
                        "feat/x": {
                            "mode": "independent",
                            "parent_anchor": "main",
                            "slug": slug,
                            "confirmed_at": "2026-08-13T10:00:00Z",
                        }
                    },
                )
                with self.assertRaises(control_state.ValidationError):
                    self.validate()

        self.write_json(
            self.kb / "_branches.json",
            {
                "feat/x": {
                    "mode": "independent",
                    "parent_anchor": "main",
                    "slug": "feat-x",
                    "confirmed_at": "2026-08-13T10:00:00Z",
                },
                "feat-x": {
                    "mode": "independent",
                    "parent_anchor": "main",
                    "slug": "feat-x",
                    "confirmed_at": "2026-08-13T10:00:00Z",
                },
            },
        )
        with self.assertRaisesRegex(control_state.ValidationError, "shared"):
            self.validate()

    def test_kbr_must_be_registered_direct_child(self) -> None:
        branch_root = self.add_independent()
        nested = branch_root / "nested"
        nested.mkdir()
        outside = self.base / "outside"
        outside.mkdir()

        for path in (nested, outside, self.kb):
            with self.subTest(path=path):
                with self.assertRaises(control_state.ValidationError):
                    self.validate(path)

    def test_branch_meta_must_match_registration(self) -> None:
        self.add_independent(meta_branch="other")

        with self.assertRaisesRegex(control_state.ValidationError, "does not match"):
            self.validate()

    def test_kb_kbr_and_control_symlinks_are_rejected(self) -> None:
        outside = self.base / "outside-control"
        outside.write_text("{}\n", encoding="utf-8")
        (self.kb / "_meta.json").unlink()
        (self.kb / "_meta.json").symlink_to(outside)
        with self.assertRaisesRegex(control_state.ValidationError, "symlink"):
            self.validate()

        (self.kb / "_meta.json").unlink()
        self.write_json(self.kb / "_meta.json", {"anchor_kind": "main", "branch": "main"})
        branch_root = self.add_independent()
        (branch_root / "_sync.json").unlink()
        (branch_root / "_sync.json").symlink_to(outside)
        with self.assertRaisesRegex(control_state.ValidationError, "symlink"):
            self.validate()

        (branch_root / "_sync.json").unlink()
        self.write_json(branch_root / "_sync.json", self.sync())
        real_root = self.base / "real-branch"
        branch_root.rename(real_root)
        branch_root.symlink_to(real_root, target_is_directory=True)
        with self.assertRaisesRegex(control_state.ValidationError, "symlink"):
            self.validate()

    def test_unregistered_or_missing_branch_directories_are_rejected(self) -> None:
        extra = self.kb / ".branches" / "unregistered"
        extra.mkdir(parents=True)
        with self.assertRaisesRegex(control_state.ValidationError, "unregistered"):
            self.validate()

        extra.rmdir()
        root = self.add_independent()
        for child in root.iterdir():
            child.unlink()
        root.rmdir()
        with self.assertRaisesRegex(control_state.ValidationError, "missing"):
            self.validate()

    def test_sync_schema_and_json_types_fail_closed(self) -> None:
        invalid_syncs = (
            self.sync(codewise_version=True),
            self.sync(worktree_count="1"),
            self.sync(known_worktrees="."),
            self.sync(known_worktrees=[".", "."]),
            self.sync(scope_root="../outside"),
            self.sync(synced_at="2026-08-13T10:00:00"),
            {**self.sync(), "unexpected": "value"},
        )
        for value in invalid_syncs:
            with self.subTest(value=value):
                self.write_json(self.kb / "_sync.json", value)
                with self.assertRaises(control_state.ValidationError):
                    self.validate()

    def test_hash_shape_and_object_existence_are_both_required(self) -> None:
        for oid in ("a" * 39, "a" * 41, "g" * 40, "a" * 40 + "^{tree}"):
            with self.subTest(oid=oid):
                self.write_json(self.kb / "_sync.json", self.sync(baseline_commit=oid))
                with self.assertRaisesRegex(control_state.ValidationError, "40/64"):
                    self.validate()

        for oid in ("a" * 40, "a" * 64):
            with self.subTest(oid=oid):
                self.write_json(self.kb / "_sync.json", self.sync(baseline_commit=oid))
                with self.assertRaisesRegex(control_state.ValidationError, "existing commit"):
                    self.validate()

    def test_forked_from_must_resolve_in_kb_repository(self) -> None:
        self.add_independent(forked_from="a" * 40)

        with self.assertRaisesRegex(control_state.ValidationError, "existing commit"):
            self.validate()

    def test_duplicate_keys_and_non_object_json_have_no_cli_traceback(self) -> None:
        (self.kb / "_branches.json").write_text(
            '{"feat/x":{"mode":"inherit","mode":"independent"}}\n',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--kb",
                str(self.kb),
                "--source-root",
                str(self.source),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate key", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        (self.kb / "_branches.json").write_text("[]\n", encoding="utf-8")
        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--kb",
                str(self.kb),
                "--source-root",
                str(self.source),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("JSON object", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_success_output_selects_branch(self) -> None:
        branch_root = self.add_independent()
        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--kb",
                str(self.kb),
                "--kbr",
                str(branch_root),
                "--source-root",
                str(self.source),
                "--pretty",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selected"]["kind"], "branch")
        self.assertEqual(payload["selected"]["branch"], "feat/long")

    def test_archived_branch_is_validated_and_marked_read_only(self) -> None:
        archive = self.archive_branch(meta_outcome="merged")

        result = self.validate()

        archived = result["branches"]["feat/long"]
        self.assertEqual(archived["path"], str(archive))
        self.assertFalse(archived["write_allowed"])
        self.assertTrue(archived["requires_reconfirmation"])
        self.assertEqual(archived["registration"]["outcome"], "merged")

    def test_archived_branch_rejects_active_directory_or_missing_archive(self) -> None:
        archive = self.archive_branch()
        active = self.kb / ".branches" / "feat-long"
        active.mkdir(parents=True)
        with self.assertRaisesRegex(control_state.ValidationError, "unregistered"):
            self.validate()

        active.rmdir()
        for child in archive.iterdir():
            child.unlink()
        archive.rmdir()
        with self.assertRaisesRegex(control_state.ValidationError, "archive directories are missing"):
            self.validate()

    def test_archived_schema_path_outcome_and_meta_are_strict(self) -> None:
        archive = self.archive_branch()
        registration = json.loads((self.kb / "_branches.json").read_text(encoding="utf-8"))

        invalid_updates = (
            {"archive_path": ".archive/nested/name"},
            {"archive_path": "../outside"},
            {"outcome": "unknown"},
            {"archived_at": "2026-08-13T10:00:00"},
        )
        for update in invalid_updates:
            with self.subTest(update=update):
                candidate = json.loads(json.dumps(registration))
                candidate["feat/long"].update(update)
                self.write_json(self.kb / "_branches.json", candidate)
                with self.assertRaises(control_state.ValidationError):
                    self.validate()

        self.write_json(self.kb / "_branches.json", registration)
        meta = json.loads((archive / "_meta.json").read_text(encoding="utf-8"))
        meta["branch"] = "other"
        self.write_json(archive / "_meta.json", meta)
        with self.assertRaisesRegex(control_state.ValidationError, "does not match"):
            self.validate()

    def test_archived_archive_paths_are_globally_unique(self) -> None:
        self.archive_branch()
        registrations = json.loads((self.kb / "_branches.json").read_text(encoding="utf-8"))
        registrations["other/branch"] = {
            "mode": "archived",
            "parent_anchor": "main",
            "slug": "other-branch",
            "confirmed_at": "2026-08-13T10:05:00+08:00",
            "archive_path": ".archive/FEAT-LONG-20260813T100000",
            "archived_at": "2026-08-13T10:11:00+08:00",
            "outcome": "abandoned",
        }
        self.write_json(self.kb / "_branches.json", registrations)

        with self.assertRaisesRegex(control_state.ValidationError, "globally unique"):
            self.validate()

    def test_unregistered_archive_directory_is_rejected(self) -> None:
        (self.kb / ".archive" / "orphan").mkdir(parents=True)

        with self.assertRaisesRegex(control_state.ValidationError, "unregistered archive"):
            self.validate()

    def test_archived_branch_can_be_reconfirmed_as_independent_with_history(self) -> None:
        archive = self.archive_branch(outcome="merged")
        archived = json.loads((self.kb / "_branches.json").read_text(encoding="utf-8"))[
            "feat/long"
        ]
        active = self.kb / ".branches" / "feat-long"
        active.mkdir(parents=True)
        self.write_json(
            active / "_meta.json",
            {
                "anchor_kind": "branch",
                "branch": "feat/long",
                "forked_from": self.kb_commit,
            },
        )
        self.write_json(active / "_sync.json", self.sync())
        self.write_json(
            self.kb / "_branches.json",
            {
                "feat/long": {
                    "mode": "independent",
                    "parent_anchor": "main",
                    "slug": "feat-long",
                    "confirmed_at": "2026-08-13T11:00:00+08:00",
                    "archive_history": [
                        {
                            "slug": archived["slug"],
                            "archive_path": archived["archive_path"],
                            "archived_at": archived["archived_at"],
                            "outcome": archived["outcome"],
                        }
                    ],
                }
            },
        )

        result = self.validate(active)

        branch = result["branches"]["feat/long"]
        self.assertEqual(result["selected"]["kind"], "branch")
        self.assertEqual(branch["path"], str(active))
        self.assertEqual(branch["archive_history"][0]["path"], str(archive))
        self.assertFalse(branch["archive_history"][0]["write_allowed"])

    def test_archived_slug_does_not_block_different_active_branch(self) -> None:
        self.archive_branch(branch="feat/long", slug="feat-long")
        active = self.kb / ".branches" / "feat-long"
        active.mkdir(parents=True)
        self.write_json(
            active / "_meta.json",
            {
                "anchor_kind": "branch",
                "branch": "feat-long",
                "forked_from": self.kb_commit,
            },
        )
        self.write_json(active / "_sync.json", self.sync())
        registrations = json.loads((self.kb / "_branches.json").read_text(encoding="utf-8"))
        registrations["feat-long"] = {
            "mode": "independent",
            "parent_anchor": "main",
            "slug": "feat-long",
            "confirmed_at": "2026-08-13T11:00:00+08:00",
        }
        self.write_json(self.kb / "_branches.json", registrations)

        result = self.validate(active)

        self.assertEqual(result["selected"]["branch"], "feat-long")
        self.assertEqual(result["branches"]["feat/long"]["registration"]["mode"], "archived")


if __name__ == "__main__":
    unittest.main()
