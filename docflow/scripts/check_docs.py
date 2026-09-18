#!/usr/bin/env python3
"""只读检查指定文档的中文一级标题及标准 Markdown 本地链接。"""
import argparse
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


def without_code(text):
    lines = []
    fence = None
    for line in text.splitlines():
        mark = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if mark:
            token = mark[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append('')
        elif fence:
            lines.append('')
        else:
            lines.append(re.sub(r'(`+).*?\1', '', line))
    return '\n'.join(lines)


def check(path):
    text = path.read_text(encoding='utf-8')
    text = without_code(text)
    issues, warnings = [], []
    title = re.search(r'^#\s+(.+)$', text, re.M)
    if not title or not re.search(r'[\u3400-\u9fff]', title[1]):
        issues.append('缺少含中文的一级标题')
    definitions = {}
    destinations = []
    for line in text.splitlines():
        definition = re.match(r'^\s{0,3}\[([^\]]+)\]:\s*(<[^>]+>|\S+)', line)
        if definition:
            definitions[definition[1].strip().casefold()] = definition[2]
        for match in re.finditer(r'!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+["\'][^\n]*["\'])?\s*\)', line):
            destinations.append(match[1])
        for match in re.finditer(r'!?\[([^\]\n]+)\]\[([^\]\n]*)\]', line):
            key = (match[2] or match[1]).strip().casefold()
            destinations.append(('reference', key))
    checked = 0
    for destination in destinations:
        if isinstance(destination, tuple):
            key = destination[1]
            if key not in definitions:
                issues.append(f'缺少引用式链接定义：{key}')
                continue
            destination = definitions[key]
        destination = destination.strip('<>')
        parsed = urlsplit(destination)
        if parsed.scheme or destination.startswith('//'):
            continue
        if parsed.fragment:
            warnings.append(f'未核验锚点：{destination}')
        if not parsed.path:
            continue
        value = unquote(parsed.path)
        if any(char in value for char in '*{}') or '$' in value:
            warnings.append(f'动态/示例路径需人工核验：{destination}')
            continue
        target = Path(value)
        if not target.is_absolute():
            target = path.parent / target
        checked += 1
        if not target.exists():
            issues.append(f'本地链接目标不存在：{destination}')
    return checked, issues, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path, help='明确指定的 Markdown 文档，不递归扫描')
    args = parser.parse_args()
    failed = False
    for path in args.files:
        try:
            count, issues, warnings = check(path)
        except (OSError, UnicodeError, ValueError) as error:
            count, issues, warnings = 0, [str(error)], []
        print(f'{path}: 检查 {count} 个本地目标，{len(issues)} 个问题')
        for issue in issues:
            print(f'  问题：{issue}')
        for warning in dict.fromkeys(warnings):
            print(f'  待人工核验：{warning}')
        failed |= bool(issues)
    print('范围：中文一级标题、常规内联/显式引用式 Markdown 本地链接；不联网、不读取目标正文。')
    print('不覆盖锚点、HTML/MDX、自定义链接语法及复杂括号嵌套；路径通过不代表语义或运行状态已核验。')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
