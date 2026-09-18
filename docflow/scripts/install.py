#!/usr/bin/env python3
"""安装/核对 DocFlow 自有规则区块与技能链接；不执行启动 hook。"""
import argparse
import difflib
import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
START = '<!-- docflow:start'
END = '<!-- docflow:end -->'
BLOCK = re.compile(r'<!-- docflow:start sha256=([0-9a-f]{64}) prefix=([012]) -->\n(.*?)<!-- docflow:end -->\n', re.S)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read(path):
    return path.read_bytes().decode('utf-8') if path.exists() else ''


def split_block(text, verify=True):
    if START not in text and END not in text:
        return text, None
    matches = list(BLOCK.finditer(text))
    if len(matches) != 1 or text.count(START) != 1 or text.count(END) != 1:
        raise ValueError('DocFlow 区块标记损坏或重复，请人工核对')
    match = matches[0]
    body = match[3]
    if verify and digest(body) != match[1]:
        raise ValueError('DocFlow 已安装区块被手动修改，拒绝覆盖/移除；请先保存并核对差异')
    prefix = int(match[2])
    pos = match.start() - prefix
    if pos < 0 or text[pos:match.start()] != '\n' * prefix:
        raise ValueError('DocFlow 区块边界被修改，请人工核对')
    return text[:pos] + text[match.end():], body


def rendered(base, body):
    if body is None:
        return base
    prefix = 0 if not base or base.endswith('\n\n') else (1 if base.endswith('\n') else 2)
    return (base + '\n' * prefix +
            f'{START} sha256={digest(body)} prefix={prefix} -->\n' + body + END + '\n')


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix='.docflow-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(text.encode('utf-8'))
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(args):
    for filename in ('RULES.md', 'SKILL.md'):
        if not (SOURCE / filename).is_file():
            raise ValueError(f'DocFlow 源文件缺失：{SOURCE / filename}')
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    codex_home = Path(args.codex_home).expanduser().resolve() if args.codex_home else (
        Path(os.environ.get('CODEX_HOME', str(home / '.codex'))).expanduser().resolve()
        if not args.home else home / '.codex')
    clients = {'claude', 'codex'} if args.clients == 'both' else {args.clients}
    claude = home / '.claude/CLAUDE.md'
    codex = codex_home / 'AGENTS.md'
    full = f'<!-- source: {SOURCE / "RULES.md"} -->\n' + (SOURCE / 'RULES.md').read_text().replace('# ', '## ', 1).rstrip() + '\n'
    shared = (f'<!-- source: {SOURCE / "RULES.md"} -->\n'
              '## 文档协作 · DocFlow\n\n'
              f'每次开发任务读取 `{claude}` 中的“文档协作 · DocFlow”规则；本会话已读且未变则不重复读。'
              '该规则规定已有文档随实现维护、新建文档先建议并获准、中文标题与时效性核对。'
              '若该文件缺失，明确说明规则入口不可用，不声称已加载；具体文档整理按需使用 `docflow` Skill。\n')
    if 'codex' in clients:
        override = codex_home / 'AGENTS.override.md'
        if override.exists() and read(override).strip():
            raise ValueError(f'{override} 优先于 AGENTS.md；请先明确规则应安装的位置')
    if args.command == 'uninstall' and clients == {'claude'}:
        _, body = split_block(read(codex))
        if body and str(claude) in body:
            raise ValueError('Codex 仍依赖共享 Claude 规则；先将 Codex 单独安装为正文，或同时解除两端')
    bodies = {}
    if 'claude' in clients:
        bodies[claude] = full
    if 'codex' in clients:
        bodies[codex] = shared if 'claude' in clients else full
    plans = []
    for path, body in bodies.items():
        if path.is_symlink():
            raise ValueError(f'全局配置是符号链接，需明确其管理方式后再安装：{path}')
        old = read(path)
        base, _ = split_block(old, verify=args.command != 'diff')
        new = rendered(base, None if args.command == 'uninstall' else body)
        plans.append((path, old, new, path.exists()))
    links = []
    if 'claude' in clients:
        links.append(home / '.claude/skills/docflow')
    if 'codex' in clients:
        links.append(home / '.agents/skills/docflow')
    # 父目录可以是既有别名（例如 ~/.agents/skills -> ~/.mirasim/skills）。
    links = list(dict.fromkeys(p.parent.resolve() / p.name for p in links))
    for path in links:
        if path.is_symlink():
            if path.resolve() != SOURCE:
                raise ValueError(f'技能链接指向其他来源，拒绝覆盖：{path}')
        elif path.exists():
            raise ValueError(f'技能位置已有目录或文件，拒绝覆盖：{path}')
    changed = any(old != new for _, old, new, _ in plans)
    changed |= any((p.is_symlink() if args.command == 'uninstall' else not p.is_symlink()) for p in links)
    if args.command in ('check', 'diff'):
        for path, old, new, _ in plans:
            print(f'{"一致" if old == new else "待更新"}: {path}')
            if args.command == 'diff':
                sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                         fromfile=str(path), tofile=str(path) + ' (DocFlow)'))
        for path in links:
            print(f'{"一致" if path.is_symlink() else "缺少链接"}: {path} -> {SOURCE}')
        return 1 if changed else 0
    # 先完成全部冲突检查，再写；发生错误时回退已完成操作。无跨进程事务保证。
    undo = []
    try:
        for path, old, new, existed in plans:
            if old == new:
                continue
            if read(path) != old:
                raise ValueError(f'配置在检查后发生变化，停止：{path}')
            undo.append(('file', path, old, existed))
            atomic_write(path, new)
        for path in links:
            if args.command == 'uninstall':
                if path.is_symlink():
                    target = path.readlink()
                    path.unlink()
                    undo.append(('link', path, target, True))
            elif not path.is_symlink():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(SOURCE, target_is_directory=True)
                undo.append(('link', path, None, False))
    except Exception:
        for kind, path, old, existed in reversed(undo):
            if kind == 'file':
                if existed:
                    atomic_write(path, old)
                elif path.exists():
                    path.unlink()
            elif existed:
                path.symlink_to(old, target_is_directory=True)
            elif path.is_symlink():
                path.unlink()
        raise
    print(('已解除' if args.command == 'uninstall' else '已安装/更新') +
          f' DocFlow（{args.clients}）；' + ('发生变更' if changed else '无需变更'))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['install', 'check', 'diff', 'uninstall'])
    parser.add_argument('--clients', choices=['both', 'claude', 'codex'], default='both')
    parser.add_argument('--home', help='隔离安装根目录；默认当前用户主目录')
    parser.add_argument('--codex-home', help='Codex 配置目录；默认遵循 CODEX_HOME')
    args = parser.parse_args()
    try:
        return run(args)
    except (ValueError, OSError, UnicodeError) as error:
        print(f'DocFlow: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
