#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("discover_sessions.py")
SPEC = importlib.util.spec_from_file_location("discover_sessions", SCRIPT)
assert SPEC and SPEC.loader
discover_sessions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(discover_sessions)


def run(*args: str) -> str:
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.repo = base / "project"
        self.worktree = base / "project-feature"
        self.repo.mkdir()
        run("git", "-C", str(self.repo), "init", "-q")
        run("git", "-C", str(self.repo), "config", "user.email", "codewise@example.invalid")
        run("git", "-C", str(self.repo), "config", "user.name", "Codewise Test")
        (self.repo / "README.md").write_text("test\n", encoding="utf-8")
        run("git", "-C", str(self.repo), "add", "README.md")
        run("git", "-C", str(self.repo), "commit", "-qm", "initial")
        run("git", "-C", str(self.repo), "worktree", "add", "-qb", "feature", str(self.worktree))

        self.claude_home = base / "claude"
        self.codex_home = base / "codex"
        self.gemini_home = base / "gemini"
        self.opencode_home = base / "opencode"
        self.cursor_home = base / "cursor"
        self.env = {
            "CODEWISE_CLAUDE_HOME": str(self.claude_home),
            "CODEWISE_CODEX_HOME": str(self.codex_home),
            "CODEWISE_GEMINI_HOME": str(self.gemini_home),
            "CODEWISE_OPENCODE_HOME": str(self.opencode_home),
            "CODEWISE_CURSOR_HOME": str(self.cursor_home),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_claude(self, cwd: Path, session_id: str, branch: str | None = None) -> None:
        # Reuse the production slug so the tests cannot drift into their own
        # encoding rule (dots and separators are both collapsed to hyphens).
        slug = discover_sessions.claude_slug(cwd)
        record = {"type": "user", "sessionId": session_id, "cwd": str(cwd), "timestamp": "2026-08-05T01:00:00Z"}
        if branch:
            record["gitBranch"] = branch
        write_jsonl(
            self.claude_home / "projects" / slug / f"{session_id}.jsonl",
            [record],
        )

    def add_codex(
        self,
        cwd: Path,
        session_id: str,
        parent_session_id: str | None = None,
        branch: str | None = None,
        commit: str | None = None,
    ) -> None:
        payload = {"id": session_id, "cwd": str(cwd), "source": "cli"}
        if parent_session_id:
            payload["session_id"] = parent_session_id
        if branch or commit:
            payload["git"] = {key: value for key, value in (("branch", branch), ("commit_hash", commit)) if value}
        write_jsonl(
            self.codex_home / "sessions" / "2026" / "08" / "05" / f"rollout-{session_id}.jsonl",
            [
                {
                    "type": "session_meta",
                    "timestamp": "2026-08-05T01:00:00Z",
                    "payload": payload,
                }
            ],
        )

    def add_gemini(self, cwd: Path, session_id: str) -> None:
        digest = discover_sessions.gemini_hash(cwd)
        path = self.gemini_home / "tmp" / digest / "chats" / f"session-{session_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sessionId": session_id, "projectHash": digest, "messages": []}), encoding="utf-8")

    def test_discovers_main_and_worktree_sessions(self) -> None:
        self.add_claude(self.repo, "claude-main")
        self.add_claude(self.worktree, "claude-worktree")
        self.add_codex(self.repo, "codex-main")
        self.add_codex(self.worktree, "codex-worktree")
        self.add_gemini(self.worktree, "gemini-worktree")

        with mock.patch.dict(os.environ, self.env, clear=False):
            baseline = run("git", "-C", str(self.repo), "rev-parse", "HEAD")
            result = discover_sessions.discover(self.repo, baseline_value=baseline)

        self.assertEqual(result["providers"], {"claude": 2, "codex": 2, "gemini": 1})
        self.assertEqual(len(result["project"]["worktrees"]), 2)
        self.assertTrue(all(item.get("merge_base") for item in result["project"]["worktrees"]))
        canonical_worktree = str(self.worktree.resolve())
        worktree_sessions = [item for item in result["sessions"] if item["cwd"] == canonical_worktree]
        self.assertEqual({item["provider"] for item in worktree_sessions}, {"claude", "codex", "gemini"})

    def test_subproject_marks_repo_root_session_for_content_check(self) -> None:
        scope = self.repo / "apps" / "web"
        scope.mkdir(parents=True)
        self.add_codex(self.repo, "repo-root")

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(scope, providers=("codex",))

        self.assertEqual(result["session_count"], 1)
        item = result["sessions"][0]
        self.assertEqual(item["relation"], "worktree-cwd")
        self.assertTrue(item["needs_content_check"])
        self.assertTrue(item["scope_root"].endswith("apps/web"))

    def test_custom_source_is_extensible_and_requires_content_check(self) -> None:
        custom = Path(self.temp.name) / "other-agent" / "session.jsonl"
        write_jsonl(custom, [{"role": "user", "content": "hello"}])
        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(
                self.repo,
                providers=(),
                custom_sources=[f"other={custom.parent}/**/*.jsonl"],
            )
        self.assertEqual(result["providers"], {"other": 1})
        self.assertTrue(result["sessions"][0]["needs_content_check"])

    def test_opencode_sqlite_uses_session_directory(self) -> None:
        database = self.opencode_home / "opencode.db"
        database.parent.mkdir(parents=True)
        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, branch TEXT, commit_hash TEXT)"
        )
        connection.execute(
            "INSERT INTO session VALUES (?, ?, ?, ?)",
            ("inside", str(self.worktree), "feature", "abc123"),
        )
        connection.execute(
            "INSERT INTO session VALUES (?, ?, ?, ?)",
            ("outside", str(Path(self.temp.name) / "other"), None, None),
        )
        connection.commit()
        connection.close()

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("opencode",))

        self.assertEqual(result["providers"], {"opencode": 1})
        self.assertEqual(result["sessions"][0]["session_id"], "inside")
        self.assertEqual(result["sessions"][0]["format"], "opencode-sqlite")
        self.assertEqual(result["sessions"][0]["branch"], "feature")
        self.assertEqual(result["sessions"][0]["session_head"], "abc123")

    def test_codex_child_rollouts_keep_unique_ids(self) -> None:
        self.add_codex(self.repo, "root", parent_session_id="root")
        self.add_codex(self.repo, "child-a", parent_session_id="root")
        self.add_codex(self.repo, "child-b", parent_session_id="root")

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("codex",))

        self.assertEqual({item["session_id"] for item in result["sessions"]}, {"root", "child-a", "child-b"})
        children = [item for item in result["sessions"] if item["session_id"].startswith("child-")]
        self.assertEqual({item["parent_session_id"] for item in children}, {"root"})

    def test_codex_git_provenance_is_preserved(self) -> None:
        baseline = run("git", "-C", str(self.repo), "rev-parse", "HEAD")
        scan_branch = run("git", "-C", str(self.repo), "branch", "--show-current")
        historical_branch = "main" if scan_branch != "main" else "feature"
        self.add_codex(self.repo, "codex-with-git", branch=historical_branch, commit=baseline)

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("codex",), baseline_value=baseline)

        item = result["sessions"][0]
        self.assertEqual(item["branch"], historical_branch)
        self.assertEqual(item["branch_source"], "session-metadata")
        self.assertEqual(item["session_head"], baseline)
        self.assertEqual(item["head_state"], "session-head-matches-scan")
        self.assertEqual(item["branch_state"], "session-branch-differs-from-scan")

    def test_claude_branch_metadata_is_preserved(self) -> None:
        self.add_claude(self.worktree, "with-branch", branch="feature")

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("claude",))

        item = result["sessions"][0]
        self.assertEqual(item["branch"], "feature")
        self.assertEqual(item["branch_source"], "session-metadata")
        self.assertEqual(item["branch_state"], "session-branch-matches-scan")

    def test_other_branch_session_is_marked_as_different(self) -> None:
        self.add_claude(self.worktree, "other-branch", branch="main")

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("claude",))

        item = result["sessions"][0]
        self.assertEqual(item["scan_branch"], "feature")
        self.assertEqual(item["branch"], "main")
        self.assertEqual(item["branch_state"], "session-branch-differs-from-scan")

    def test_late_branch_metadata_is_not_lost(self) -> None:
        slug = discover_sessions.claude_slug(self.worktree)
        path = self.claude_home / "projects" / slug / "late-branch.jsonl"
        write_jsonl(
            path,
            [
                {"type": "user", "sessionId": "late-branch", "cwd": str(self.worktree)},
                {"type": "assistant", "gitBranch": "feature"},
            ],
        )

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("claude",))

        self.assertEqual(result["sessions"][0]["branch"], "feature")

    def test_claude_slug_collapses_dots_like_claude_code(self) -> None:
        # `.claude/worktrees/<name>` is where Claude Code puts its own
        # worktrees, so a slug that keeps the dot finds no project directory.
        # /Users is used because macOS resolves /tmp to /private/tmp.
        slug = discover_sessions.claude_slug(Path("/Users/x/proj/.claude/worktrees/wt"))
        self.assertEqual(slug, "-Users-x-proj--claude-worktrees-wt")
        self.assertNotIn(".", slug)

    def test_nested_worktree_keeps_its_own_sessions(self) -> None:
        # A worktree inside the main tree must claim its sessions itself;
        # attributing them to the parent would report the wrong branch and
        # would also clear needs_content_check.
        nested = self.repo / ".claude" / "worktrees" / "nested"
        run("git", "-C", str(self.repo), "worktree", "add", "-qb", "nested-topic", str(nested))
        self.add_claude(nested, "claude-nested", branch="nested-topic")

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("claude",))

        item = next(s for s in result["sessions"] if s["session_id"] == "claude-nested")
        self.assertEqual(item["worktree_root"], str(discover_sessions.lexical_path(nested)))
        self.assertEqual(item["scan_branch"], "nested-topic")
        self.assertEqual(item["branch_state"], "session-branch-matches-scan")

    def test_known_worktree_readmits_pruned_sessions(self) -> None:
        # `git worktree remove` drops the registry entry, and with it every
        # session whose cwd lived inside that tree.  Paths recorded by an
        # earlier run must bring those sessions back.
        pruned = Path(self.temp.name) / "project-pruned"
        run("git", "-C", str(self.repo), "worktree", "add", "-qb", "pruned-topic", str(pruned))
        self.add_claude(pruned, "claude-pruned", branch="pruned-topic")
        run("git", "-C", str(self.repo), "worktree", "remove", "--force", str(pruned))

        with mock.patch.dict(os.environ, self.env, clear=False):
            without = discover_sessions.discover(self.repo, providers=("claude",))
            with_known = discover_sessions.discover(
                self.repo, providers=("claude",), known_worktrees=[str(pruned)]
            )

        self.assertNotIn("claude-pruned", [s["session_id"] for s in without["sessions"]])
        recovered = next(s for s in with_known["sessions"] if s["session_id"] == "claude-pruned")
        self.assertEqual(recovered["worktree_root"], str(discover_sessions.lexical_path(pruned)))
        # No HEAD/branch for a tree that is gone, so judgement stays conservative.
        self.assertIsNone(recovered["scan_branch"])
        self.assertEqual(recovered["branch_state"], "unknown-branch")
        # Persisted relative so no username-bearing path is ever committed.
        self.assertIn("../project-pruned", with_known["project"]["known_worktrees"])

    def test_known_worktrees_round_trip_as_relative_paths(self) -> None:
        # What the knowledge base stores must be accepted verbatim next run.
        pruned = Path(self.temp.name) / "project-relative"
        run("git", "-C", str(self.repo), "worktree", "add", "-qb", "relative-topic", str(pruned))
        self.add_claude(pruned, "claude-relative", branch="relative-topic")
        run("git", "-C", str(self.repo), "worktree", "remove", "--force", str(pruned))

        with mock.patch.dict(os.environ, self.env, clear=False):
            first = discover_sessions.discover(
                self.repo, providers=("claude",), known_worktrees=[str(pruned)]
            )
            # Feed back exactly what the first run told us to persist.
            second = discover_sessions.discover(
                self.repo,
                providers=("claude",),
                known_worktrees=first["project"]["known_worktrees"],
            )

        self.assertIn("claude-relative", [s["session_id"] for s in second["sessions"]])
        self.assertEqual(
            sorted(first["project"]["known_worktrees"]),
            sorted(second["project"]["known_worktrees"]),
        )


    def test_detached_head_is_not_a_branch(self) -> None:
        # Clients record the literal "HEAD" while detached.  Accepting it would
        # fake a branch switch (main -> HEAD) and feed a non-branch into
        # attribution.
        self.assertIsNone(discover_sessions.normalize_branch("HEAD"))
        self.assertIsNone(discover_sessions.normalize_branch("refs/heads/HEAD"))
        self.assertEqual(discover_sessions.normalize_branch("refs/heads/main"), "main")

    def test_resumed_session_reports_every_branch(self) -> None:
        # A session resumed across a branch switch must expose the whole
        # timeline; a single value would mislabel most of its signals.
        slug = discover_sessions.claude_slug(self.repo)
        write_jsonl(
            self.claude_home / "projects" / slug / "resumed.jsonl",
            [
                {"type": "user", "sessionId": "resumed", "cwd": str(self.repo), "gitBranch": "main"},
                {"type": "assistant", "gitBranch": "main"},
                {"type": "user", "gitBranch": "feature"},
                {"type": "assistant", "gitBranch": "HEAD"},
                {"type": "user", "gitBranch": "hotfix"},
            ],
        )

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("claude",))

        item = next(s for s in result["sessions"] if s["session_id"] == "resumed")
        self.assertEqual(item["branches"], ["main", "feature", "hotfix"])
        self.assertTrue(item["multi_branch"])

    def test_single_branch_session_is_not_flagged_multi(self) -> None:
        self.add_claude(self.repo, "single", branch="main")

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("claude",))

        item = next(s for s in result["sessions"] if s["session_id"] == "single")
        self.assertEqual(item["branches"], ["main"])
        self.assertFalse(item["multi_branch"])


    def test_known_worktrees_stay_stable_across_worktrees(self) -> None:
        # The knowledge base is shared by every worktree, so a path persisted
        # from one tree must resolve identically when read back from another.
        # Basing it on --show-toplevel instead of the main worktree invents
        # ghost paths one way and corrupts real ones the other.
        pruned = Path(self.temp.name) / "project-shared"
        run("git", "-C", str(self.repo), "worktree", "add", "-qb", "shared-topic", str(pruned))
        self.add_claude(pruned, "claude-shared", branch="shared-topic")
        run("git", "-C", str(self.repo), "worktree", "remove", "--force", str(pruned))

        with mock.patch.dict(os.environ, self.env, clear=False):
            first = discover_sessions.discover(
                self.repo, providers=("claude",), known_worktrees=[str(pruned)]
            )
            kw1 = first["project"]["known_worktrees"]
            # Feed it back from the linked worktree, then from the main tree.
            second = discover_sessions.discover(
                self.worktree, providers=("claude",), known_worktrees=kw1
            )
            kw2 = second["project"]["known_worktrees"]
            third = discover_sessions.discover(
                self.repo, providers=("claude",), known_worktrees=kw2
            )

        self.assertEqual(kw1, kw2)
        self.assertEqual(kw2, third["project"]["known_worktrees"])
        # The stale entry survives the round trip rather than becoming garbage.
        self.assertIn("claude-shared", [s["session_id"] for s in second["sessions"]])
        self.assertEqual(
            second["project"]["main_worktree"], str(discover_sessions.lexical_path(self.repo))
        )

    def test_project_directory_outranks_recorded_cwd(self) -> None:
        # Claude Code picks the project directory from the cwd the session
        # started in, while metadata_from_jsonl returns the FIRST cwd in the
        # file.  For a session that entered a worktree partway through those
        # disagree, and the recorded cwd would hand the whole session to the
        # parent tree — wrong branch, and needs_content_check cleared.
        slug = discover_sessions.claude_slug(self.worktree)
        write_jsonl(
            self.claude_home / "projects" / slug / "entered-worktree.jsonl",
            [
                {"type": "user", "sessionId": "entered", "cwd": str(self.repo), "gitBranch": "master"},
                {"type": "assistant", "cwd": str(self.worktree), "gitBranch": "feature"},
            ],
        )

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("claude",))

        item = next(s for s in result["sessions"] if s["session_id"] == "entered")
        self.assertEqual(item["worktree_root"], str(discover_sessions.lexical_path(self.worktree)))
        self.assertEqual(item["relation"], "scope-project-dir")
        self.assertEqual(item["scan_branch"], "feature")
        # Both branches still surface so the caller can segment the session.
        self.assertTrue(item["multi_branch"])


    def test_workflow_records_are_excluded_by_default(self) -> None:
        # A workflow writes one agent-*.jsonl per subagent plus a journal.jsonl
        # into the project directory.  Measured on a real project these were 12
        # of 13 "sessions" — counting them independently inflates the count and
        # re-reads work that already flowed back into the parent session.
        slug = discover_sessions.claude_slug(self.repo)
        proj = self.claude_home / "projects" / slug
        self.add_claude(self.repo, "real-session")
        for name in ("agent-a1.jsonl", "agent-a2.jsonl", "journal.jsonl"):
            write_jsonl(proj / "wf_abc123" / name, [
                {"type": "user", "sessionId": name, "cwd": str(self.repo)},
            ])

        with mock.patch.dict(os.environ, self.env, clear=False):
            default = discover_sessions.discover(self.repo, providers=("claude",))
            included = discover_sessions.discover(
                self.repo, providers=("claude",), include_derived=True
            )

        self.assertEqual([s["session_id"] for s in default["sessions"]], ["real-session"])
        self.assertEqual(len(included["sessions"]), 4)

    def test_ordinary_nested_files_are_still_discovered(self) -> None:
        # Only wf_*/ records are derived; genuine nested transcripts must stay.
        slug = discover_sessions.claude_slug(self.repo)
        write_jsonl(
            self.claude_home / "projects" / slug / "subdir" / "nested.jsonl",
            [{"type": "user", "sessionId": "nested-real", "cwd": str(self.repo)}],
        )

        with mock.patch.dict(os.environ, self.env, clear=False):
            result = discover_sessions.discover(self.repo, providers=("claude",))

        self.assertIn("nested-real", [s["session_id"] for s in result["sessions"]])


if __name__ == "__main__":
    unittest.main()
