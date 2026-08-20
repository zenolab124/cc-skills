#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("branch_manifest.py")
SPEC = importlib.util.spec_from_file_location("branch_manifest", SCRIPT)
assert SPEC and SPEC.loader
branch_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(branch_manifest)


class BranchManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.kb = Path(self.temp.name) / "kb"
        self.root = self.kb / ".branches" / "feat-x"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, relative: str, text: str = "entry\n") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_only_six_category_markdown_files_are_merge_inputs(self) -> None:
        self.write("domains/auth/login.md")
        self.write("pitfalls/race.md")
        self.write("domains/auth/raw.txt")
        self.write("INDEX.md")
        self.write("_meta.json", "{}\n")
        self.write("_sync.json", "{}\n")
        self.write("_deleted", "shared/old.md\n")

        result = branch_manifest.build_manifest(self.root, self.kb)

        self.assertEqual(result["entries"], ["domains/auth/login.md", "pitfalls/race.md"])
        self.assertEqual(result["deletions"], ["shared/old.md"])
        self.assertIn("INDEX.md", result["ignored_controls"])
        self.assertIn("_meta.json", result["ignored_controls"])

    def test_any_invalid_deleted_line_rejects_entire_manifest(self) -> None:
        invalid = (
            "domains/valid.md\n"
            "../INDEX.md\n"
            "/absolute.md\n"
            "domains/not-markdown.txt\n"
        )
        self.write("_deleted", invalid)

        with self.assertRaisesRegex(branch_manifest.ValidationError, "line 2"):
            branch_manifest.build_manifest(self.root, self.kb)

    def test_windows_absolute_and_surrounding_space_are_rejected(self) -> None:
        for value in (r"C:\\repo\\domains\\x.md", " domains/x.md"):
            with self.subTest(value=value):
                with self.assertRaises(branch_manifest.ValidationError):
                    branch_manifest.validate_entry_path(value)

    def test_control_separator_cannot_turn_into_two_valid_deletions(self) -> None:
        self.write("_deleted", "domains/a.md\x1edomains/b.md\n")

        with self.assertRaisesRegex(branch_manifest.ValidationError, "control character"):
            branch_manifest.build_manifest(self.root, self.kb)

    def test_entry_cannot_also_be_marked_deleted(self) -> None:
        self.write("domains/x.md")
        self.write("_deleted", "domains/x.md\n")

        with self.assertRaisesRegex(branch_manifest.ValidationError, "both present"):
            branch_manifest.build_manifest(self.root, self.kb)

    def test_symlinked_entry_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        target = self.root / "domains" / "escape.md"
        target.parent.mkdir(parents=True)
        target.symlink_to(outside)

        with self.assertRaisesRegex(branch_manifest.ValidationError, "regular file"):
            branch_manifest.build_manifest(self.root, self.kb)

    def test_branch_root_symlink_cannot_escape_kb(self) -> None:
        outside = Path(self.temp.name) / "outside"
        (outside / "domains").mkdir(parents=True)
        (outside / "domains" / "x.md").write_text("outside\n", encoding="utf-8")
        self.root.rmdir()
        self.root.symlink_to(outside)

        with self.assertRaisesRegex(branch_manifest.ValidationError, "must not be a symlink"):
            branch_manifest.build_manifest(self.root, self.kb)

    def test_dangling_category_symlink_is_rejected(self) -> None:
        (self.root / "domains").symlink_to(Path(self.temp.name) / "missing-domains")

        with self.assertRaisesRegex(branch_manifest.ValidationError, "not a real directory"):
            branch_manifest.build_manifest(self.root, self.kb)

    def test_documented_cli_shape_accepts_kb_and_branch_root(self) -> None:
        self.write("domains/x.md")

        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                str(self.root),
                "--kb",
                str(self.kb),
                "--pretty",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )

        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["entries"], ["domains/x.md"])
        self.assertEqual(manifest["kb"], str(self.kb))


if __name__ == "__main__":
    unittest.main()
