"""DocFlow 安装边界与文档诊断的隔离验证；不修改真实用户配置。"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install
import check_docs


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.claude = self.home / '.claude/CLAUDE.md'
        self.codex = self.home / '.codex/AGENTS.md'
        for p in [self.claude, self.codex]:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes('既有规则，保留原样\r\n'.encode())
        self.original = {p: p.read_bytes() for p in [self.claude, self.codex]}

    def run_action(self, command, clients='both'):
        args = argparse.Namespace(command=command, clients=clients, home=str(self.home), codex_home=None)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return install.run(args)

    def test_install_check_repeat_uninstall_preserve_other_rules(self):
        self.assertEqual(self.run_action('check'), 1)
        self.run_action('install')
        self.assertEqual(self.run_action('check'), 0)
        installed = {p: p.read_bytes() for p in self.original}
        self.run_action('install')
        self.assertEqual(installed, {p: p.read_bytes() for p in self.original})
        self.run_action('uninstall')
        self.assertEqual(self.original, {p: p.read_bytes() for p in self.original})
        self.assertFalse((self.home / '.agents/skills/docflow').is_symlink())

    def test_tamper_refuses_all_mutations(self):
        self.run_action('install')
        self.codex.write_text(self.codex.read_text().replace('文档协作', '本地编辑', 1))
        old = self.claude.read_bytes()
        tampered = self.codex.read_bytes()
        self.assertEqual(self.run_action('diff'), 1)
        self.assertEqual(tampered, self.codex.read_bytes())
        with self.assertRaisesRegex(ValueError, '手动修改'):
            self.run_action('uninstall')
        self.assertEqual(old, self.claude.read_bytes())

    def test_foreign_skill_preflight_writes_nothing(self):
        target = self.home / '.agents/skills/docflow'
        target.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, '已有目录'):
            self.run_action('install')
        self.assertEqual(self.original, {p: p.read_bytes() for p in self.original})

    def test_symlink_failure_rolls_back_config_and_links(self):
        real = Path.symlink_to
        def fail_second(path, target, **kwargs):
            if '.agents' in str(path):
                raise OSError('模拟权限错误')
            return real(path, target, **kwargs)
        with patch.object(Path, 'symlink_to', fail_second):
            with self.assertRaises(OSError):
                self.run_action('install')
        self.assertEqual(self.original, {p: p.read_bytes() for p in self.original})
        self.assertFalse((self.home / '.claude/skills/docflow').exists())

    def test_parent_alias_and_shared_dependency(self):
        alias = self.home / '.agents/skills'
        target = self.home / '.mirasim/skills'
        target.mkdir(parents=True)
        alias.parent.mkdir(parents=True)
        alias.symlink_to(target, target_is_directory=True)
        self.run_action('install')
        self.assertTrue((target / 'docflow').is_symlink())
        with self.assertRaisesRegex(ValueError, '仍依赖'):
            self.run_action('uninstall', 'claude')
        self.run_action('install', 'codex')
        self.run_action('uninstall', 'claude')
        self.assertEqual(self.run_action('check', 'codex'), 0)

    def test_rule_source_change_is_reported_then_updated(self):
        source = (self.home / 'source').resolve()
        source.mkdir()
        (source / 'SKILL.md').write_text('# 测试技能\n')
        rules = source / 'RULES.md'
        rules.write_text('# 测试规则\n第一版\n')
        with patch.object(install, 'SOURCE', source):
            self.run_action('install')
            rules.write_text('# 测试规则\n第二版\n')
            self.assertEqual(self.run_action('check'), 1)
            old = self.claude.read_bytes()
            self.assertEqual(self.run_action('diff'), 1)
            self.assertEqual(old, self.claude.read_bytes())
            self.run_action('install')
            self.assertEqual(self.run_action('check'), 0)

    def test_override_refuses_false_install_success(self):
        (self.codex.parent / 'AGENTS.override.md').write_text('其他规则')
        with self.assertRaisesRegex(ValueError, '优先于'):
            self.run_action('install')
        self.assertEqual(self.original, {p: p.read_bytes() for p in self.original})


class DocumentTests(unittest.TestCase):
    def test_paths_references_fences_and_warnings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '有 空格.py').write_text('')
            doc = root / '功能.md'
            doc.write_text('# 中文功能\n[代码](<有 空格.py>)\n[源码][ref]\n'
                           '[ref]: 有%20空格.py\n[锚点](#说明)\n'
                           '```md\n[示例](missing.py)\n```\n')
            checked, issues, warnings = check_docs.check(doc)
            self.assertEqual(checked, 2)
            self.assertEqual(issues, [])
            self.assertEqual(len(warnings), 1)
            doc.write_text('# English\n[入口](missing.py)\n[未知][absent]\n')
            _, issues, _ = check_docs.check(doc)
            self.assertEqual(len(issues), 3)


if __name__ == '__main__':
    unittest.main()
