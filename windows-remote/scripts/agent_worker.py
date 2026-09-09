"""One Windows desktop job: run/resume Codex, persist events and bounded status."""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out'}


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def validate(request):
    if request.get('schemaVersion') != 1 or not re.fullmatch('[a-f0-9]{32}', request.get('runId', '')):
        raise ValueError('invalid request identity')
    if request.get('sandbox') not in {'read-only', 'workspace-write'}:
        raise ValueError('unsupported sandbox')
    if not isinstance(request.get('prompt'), str) or not 1 <= len(request['prompt']) <= 100_000:
        raise ValueError('prompt must contain 1 to 100000 characters')
    if type(request.get('timeout')) is not int or not 30 <= request['timeout'] <= 7200:
        raise ValueError('timeout must be between 30 and 7200 seconds')
    if request.get('threadId') and not re.fullmatch('[a-f0-9-]{36}', request['threadId']):
        raise ValueError('invalid thread ID')


def command(request, folder):
    args = [request['codex'], 'exec', '-c', 'approval_policy="never"',
            '-c', 'sandbox_mode=' + json.dumps(request['sandbox'])]
    if request['sandbox'] == 'workspace-write':
        args += ['--add-dir', str(folder / 'artifacts')]
    if request.get('threadId'):
        args += ['resume']
    args += ['--json', '--output-last-message', str(folder / 'reply.md'), '--skip-git-repo-check']
    if request.get('threadId'):
        args += [request['threadId']]
    return args + ['-']


def stop_tree(process):
    if process.poll() is None:
        subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        process.wait(timeout=15)


def main(path):
    folder = path.parent
    request = json.loads(path.read_text(encoding='utf-8'))
    validate(request)
    # Task Scheduler may deliver a start more than once. Never execute it twice.
    try:
        fd = os.open(folder / 'started', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return 0
    state = {'schemaVersion': 1, 'runId': request['runId'], 'status': 'starting',
             'threadId': request.get('threadId'), 'cwd': request['cwd'],
             'sandbox': request['sandbox'], 'startedAt': now(), 'updatedAt': now(),
             'workerPid': os.getpid(), 'lastMessage': '', 'eventCount': 0}
    locks = folder.parent.parent / 'locks'
    locks.mkdir(exist_ok=True)
    lease = locks / (request.get('threadId') or request['runId'])
    process = None
    leased = False
    try:
        fd = os.open(lease, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, request['runId'].encode()); os.close(fd); leased = True
        atomic_json(folder / 'status.json', state)
        (folder / 'artifacts').mkdir(exist_ok=True)
        if (folder / 'cancel').exists():
            state['status'] = 'cancelled'
            return 1
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        # Make the selected real runtime available to the remote agent; avoid WindowsApps aliases.
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
        header = ('你是运行在 Windows 本机的协作 Agent，由 Mac 主 Agent 派发任务。'
                  '先读取适用的 AGENTS.md、CLAUDE.md、CLAUDE.local.md、.claude/CLAUDE.md 与必要项目文档。'
                  '用户要求用中文汇报。只执行下方已授权范围，不自动扩大为审查、构建、发布或游戏操作。'
                  '不要读取或输出凭据。需要权限或缺失条件时明确报告，不绕过限制。'
                  'PowerShell 5 读取 UTF-8 文本请显式指定 -Encoding UTF8；Python 用 -B 避免字节码写入。'
                  '最终回复说明结论、实际执行与证据、未证实事项；这是可追问的持久会话。\n'
                  f'本轮权限：{request["sandbox"]}。产物目录（仅写入已授权产物）：{folder / "artifacts"}\n\n')
        with (folder / 'stderr.log').open('w', encoding='utf-8') as errors, \
                (folder / 'events.jsonl').open('w', encoding='utf-8') as events:
            process = subprocess.Popen(command(request, folder), cwd=request['cwd'], env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True,
                encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
            state.update(status='running', agentPid=process.pid)
            atomic_json(folder / 'status.json', state)
            process.stdin.write(header + request['prompt']); process.stdin.close()
            inbox = queue.Queue()
            def read():
                for line in process.stdout:
                    inbox.put(line)
                inbox.put(None)
            threading.Thread(target=read, daemon=True).start()
            deadline = time.monotonic() + request['timeout']
            turn_done = False
            while True:
                if (folder / 'cancel').exists() or time.monotonic() >= deadline:
                    state['status'] = 'cancelled' if (folder / 'cancel').exists() else 'timed_out'
                    stop_tree(process)
                    break
                try:
                    line = inbox.get(timeout=0.25)
                except queue.Empty:
                    continue
                if line is None:
                    break
                events.write(line); events.flush()
                state['eventCount'] += 1
                state['updatedAt'] = now()
                try:
                    event = json.loads(line)
                    kind = event.get('type')
                    if kind == 'thread.started':
                        state['threadId'] = event['thread_id']
                    if kind == 'item.completed' and event.get('item', {}).get('type') == 'agent_message':
                        state['lastMessage'] = event['item'].get('text', '')[-8000:]
                    if kind == 'turn.completed':
                        turn_done = True
                        state['usage'] = event.get('usage')
                    if kind in {'turn.failed', 'error'}:
                        state['error'] = event.get('error') or event.get('message')
                except (ValueError, KeyError, TypeError):
                    state['parseWarning'] = True
                atomic_json(folder / 'status.json', state)
            code = process.wait(timeout=15)
            state['exitCode'] = code
            if state['status'] not in TERMINAL:
                state['status'] = 'completed' if code == 0 and turn_done and (folder / 'reply.md').exists() else 'failed'
    except Exception as error:
        state.update(status='failed', error=str(error))
        if process:
            stop_tree(process)
    finally:
        if leased:
            lease.unlink(missing_ok=True)
        state.update(updatedAt=now(), completedAt=now())
        atomic_json(folder / 'status.json', state)
        # Removing our one-shot registration does not stop the active action.
        subprocess.run(['schtasks.exe', '/Delete', '/TN', 'MacWindowsBridge-' + request['runId'], '/F'],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return 0 if state['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
