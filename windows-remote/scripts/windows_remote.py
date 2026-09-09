#!/usr/bin/env python3
"""Mac → Windows commands, files, and persistent asynchronous Codex conversations."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import agent_worker
from transport import check_transport, scp, ssh

STATE = Path.home() / '.codex' / 'windows-remote'


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


def host(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', value):
        raise ValueError('host must be a trusted SSH alias')
    return value


def doctor(peer, cwd=None):
    script = r'''
$base=Join-Path $env:LOCALAPPDATA 'MacWindowsBridge'
$codex=@(Get-ChildItem -LiteralPath (Join-Path $base 'cli\node_modules\@openai') -Filter codex.exe -Recurse -ErrorAction SilentlyContinue)|Select-Object -First 1 -ExpandProperty FullName
if(!$codex){$c=Get-Command codex.exe -ErrorAction SilentlyContinue;if($c){$codex=$c.Source}}
$candidates=@((Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'))
if(CWD){$candidates+=Join-Path CWD 'src-tauri\target\automation-runtime\python\python.exe'}
$p=Get-Command python.exe -ErrorAction SilentlyContinue;if($p -and $p.Source -notlike '*WindowsApps*'){$candidates+=$p.Source}
$python=$candidates|Where-Object {Test-Path -LiteralPath $_}|Select-Object -First 1
$version=$null;$login=$null
if($codex){$version=(& $codex --version|Out-String).Trim();$ErrorActionPreference='Continue';$login=(& $codex login status 2>&1|Out-String).Trim();$ErrorActionPreference='Stop'}
[pscustomobject]@{host=$env:COMPUTERNAME;user=([Security.Principal.WindowsIdentity]::GetCurrent().Name);profile=$env:USERPROFILE;base=$base;python=$python;codex=$codex;version=$version;login=$login;interactive=@(Get-Process explorer -ErrorAction SilentlyContinue|Select-Object -ExpandProperty SessionId)}|ConvertTo-Json -Depth 3 -Compress
'''.replace('CWD', quote(cwd) if cwd else '$null')
    return json.loads(check_transport(ssh(peer, script)))


def load_run(value):
    if not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('run must be the runId returned by start/reply')
    folder = STATE / 'runs' / value
    meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
    if meta['runId'] != value:
        raise ValueError('local run identity differs')
    host(meta['host'])
    return folder, meta


def refresh(run_id):
    folder, meta = load_run(run_id)
    remote = quote(meta['remoteFolder'])
    script = f'''
$folder={remote};$file=Join-Path $folder 'status.json'
$status=if(Test-Path -LiteralPath $file){{Get-Content -LiteralPath $file -Raw -Encoding UTF8|ConvertFrom-Json}}else{{[pscustomobject]@{{runId={quote(run_id)};status='queued'}}}}
$task=Get-ScheduledTask -TaskName {quote('MacWindowsBridge-' + run_id)} -ErrorAction SilentlyContinue
[pscustomobject]@{{status=$status;taskState=if($task){{$task.State.ToString()}}else{{$null}}}}|ConvertTo-Json -Depth 10 -Compress
'''
    response = json.loads(check_transport(ssh(meta['host'], script)))
    state = response['status']
    if state['runId'] != run_id:
        raise ValueError('remote run identity differs')
    state['taskState'] = response['taskState']
    if state['status'] not in agent_worker.TERMINAL and response['taskState'] != 'Running':
        # Starting a scheduled task can take a few seconds. Do not claim completion on a lost worker.
        if time.time() - meta['createdUnix'] > 30:
            state['status'] = 'worker_unavailable'
    state['localOutput'] = str(folder)
    agent_worker.atomic_json(folder / 'status.json', state)
    return state


def collect(run_id):
    folder, meta = load_run(run_id)
    state = refresh(run_id)
    if state['status'] not in agent_worker.TERMINAL:
        return state
    script = '$folder=' + quote(meta['remoteFolder']) + r''';@('reply.md','events.jsonl','stderr.log')|ForEach-Object {$p=Join-Path $folder $_;if(Test-Path -LiteralPath $p){$f=Get-Item -LiteralPath $p;[pscustomobject]@{name=$_;size=$f.Length;sha256=(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower()}}}|ConvertTo-Json -Compress'''
    listing = json.loads(check_transport(ssh(meta['host'], script)) or '[]')
    listing = listing if isinstance(listing, list) else [listing]
    for entry in listing:
        if entry['name'] not in {'reply.md', 'events.jsonl', 'stderr.log'} or not 0 <= entry['size'] <= 64 * 1024 * 1024:
            raise ValueError('invalid output file')
        path = folder / entry['name']
        scp(meta['host'], path, meta['remoteFolder'] + '\\' + entry['name'], download=True)
        if path.stat().st_size != entry['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('downloaded output differs')
    if (folder / 'reply.md').exists():
        state['replyFile'] = str(folder / 'reply.md')
    # The desktop's limited token may not be allowed to delete a task registered via SSH.
    # A terminal worker has exited its agent process; unregister only this run's known task.
    task_name = quote('MacWindowsBridge-' + run_id)
    check_transport(ssh(meta['host'], f'if(Get-ScheduledTask -TaskName {task_name} -ErrorAction SilentlyContinue){{Unregister-ScheduledTask -TaskName {task_name} -Confirm:$false}}'))
    state['temporaryTaskRemoved'] = True
    state['taskState'] = None
    agent_worker.atomic_json(folder / 'status.json', state)
    return state


def start(args, previous=None):
    thread_id = None
    if previous:
        _, meta = load_run(previous)
        prior = refresh(previous)
        if prior['status'] not in agent_worker.TERMINAL or not prior.get('threadId'):
            raise ValueError('reply requires a finished run with a known threadId')
        thread_id = prior['threadId']
        args.host, args.cwd = meta['host'], meta['cwd']
        args.sandbox = args.sandbox or meta['sandbox']
    args.sandbox = args.sandbox or 'read-only'
    if not args.cwd:
        raise ValueError('--cwd must name the Windows project directory')
    prompt = Path(args.prompt_file).read_text(encoding='utf-8')
    peer = doctor(host(args.host), args.cwd)
    if not peer.get('codex') or not peer.get('python') or not peer.get('interactive'):
        raise RuntimeError('Windows needs a usable Codex CLI, Python, and a logged-in desktop; run doctor')
    run_id = uuid.uuid4().hex
    remote_folder = peer['base'] + '\\runs\\' + run_id
    folder = STATE / 'runs' / run_id
    folder.mkdir(parents=True, mode=0o700)
    request = {'schemaVersion': 1, 'runId': run_id, 'cwd': args.cwd, 'codex': peer['codex'],
               'prompt': prompt, 'sandbox': args.sandbox, 'timeout': args.timeout, 'threadId': thread_id}
    agent_worker.validate(request)
    meta = {'runId': run_id, 'host': args.host, 'cwd': args.cwd, 'sandbox': args.sandbox,
            'remoteFolder': remote_folder, 'createdUnix': time.time(), 'previousRun': previous}
    agent_worker.atomic_json(folder / 'meta.json', meta)
    agent_worker.atomic_json(folder / 'request.json', request)
    check_transport(ssh(args.host, 'if(!(Test-Path -LiteralPath ' + quote(args.cwd) + ")){throw 'cwd unavailable'};"
        + 'New-Item -ItemType Directory -Path ' + quote(remote_folder) + ' -ErrorAction Stop|Out-Null'))
    worker = Path(__file__).with_name('agent_worker.py')
    scp(args.host, worker, remote_folder + '\\agent_worker.py')
    scp(args.host, folder / 'request.json', remote_folder + '\\request.json')
    digest = hashlib.sha256(worker.read_bytes()).hexdigest()
    # WScript starts the real Python without a flashing console window.
    command = subprocess.list2cmdline([peer['python'], '-B', '-X', 'utf8', remote_folder + '\\agent_worker.py', remote_folder + '\\request.json'])
    with tempfile.TemporaryDirectory(prefix='windows-remote-launch-') as tmp:
        vbs = Path(tmp) / 'launch.vbs'
        vbs.write_text('Set sh = CreateObject("WScript.Shell")\nWScript.Quit sh.Run("' + command.replace('"', '""') + '", 0, True)\n', encoding='utf-16')
        scp(args.host, vbs, remote_folder + '\\launch.vbs')
    launcher_arguments = quote(subprocess.list2cmdline(['//B', '//NoLogo', remote_folder + '\\launch.vbs']))
    script = f'''
$folder={quote(remote_folder)}
if((Get-FileHash -LiteralPath (Join-Path $folder 'agent_worker.py') -Algorithm SHA256).Hash.ToLower() -ne {quote(digest)}){{throw 'worker hash differs'}}
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$action=New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\\wscript.exe') -Argument {launcher_arguments}
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds {args.timeout + 60}) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName {quote('MacWindowsBridge-' + run_id)} -Action $action -Principal $principal -Settings $settings|Out-Null
Start-ScheduledTask -TaskName {quote('MacWindowsBridge-' + run_id)}
'''
    check_transport(ssh(args.host, script))
    return {'runId': run_id, 'status': 'queued', 'threadId': thread_id, 'localOutput': str(folder)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('doctor'); p.add_argument('--host', default='PC'); p.add_argument('--cwd')
    p = sub.add_parser('exec'); p.add_argument('--host', default='PC'); p.add_argument('--script-file', required=True); p.add_argument('--timeout', type=int, default=45)
    for action in ['put', 'get']:
        p = sub.add_parser(action); p.add_argument('--host', default='PC'); p.add_argument('--local', required=True); p.add_argument('--remote', required=True)
    for action in ['start', 'reply']:
        p = sub.add_parser(action)
        if action == 'start':
            p.add_argument('--host', default='PC'); p.add_argument('--cwd', required=True)
        else:
            p.add_argument('run_id')
        p.add_argument('--prompt-file', required=True)
        p.add_argument('--sandbox', choices=['read-only', 'workspace-write'])
        p.add_argument('--timeout', type=int, default=900)
    for action in ['status', 'collect', 'cancel', 'wait']:
        p = sub.add_parser(action); p.add_argument('run_id')
        if action == 'wait': p.add_argument('--seconds', type=int, choices=range(1, 56), default=45, metavar='1..55')
    args = parser.parse_args()
    if args.action == 'doctor':
        emit(doctor(host(args.host), args.cwd))
    elif args.action == 'exec':
        result = ssh(host(args.host), Path(args.script_file).read_text(encoding='utf-8'), timeout=args.timeout)
        emit(result); return result['returncode']
    elif args.action in {'put', 'get'}:
        scp(host(args.host), Path(args.local).resolve(), args.remote, download=args.action == 'get')
        emit({'ok': True, 'action': args.action})
    elif args.action in {'start', 'reply'}:
        emit(start(args, getattr(args, 'run_id', None)))
    elif args.action == 'status':
        emit(refresh(args.run_id))
    elif args.action == 'collect':
        emit(collect(args.run_id))
    elif args.action == 'wait':
        deadline = time.monotonic() + args.seconds
        while True:
            state = refresh(args.run_id)
            if state['status'] in agent_worker.TERMINAL or state['status'] == 'worker_unavailable':
                emit(collect(args.run_id)); break
            if time.monotonic() >= deadline:
                emit(state); break
            time.sleep(min(3, max(0, deadline - time.monotonic())))
    elif args.action == 'cancel':
        _, meta = load_run(args.run_id)
        state = refresh(args.run_id)
        if state['status'] not in agent_worker.TERMINAL:
            check_transport(ssh(meta['host'], 'New-Item -ItemType File -Force -Path ' + quote(meta['remoteFolder'] + '\\cancel') + '|Out-Null'))
        emit({'runId': args.run_id, 'cancelRequested': state['status'] not in agent_worker.TERMINAL, 'status': state['status']})
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        emit({'ok': False, 'error': str(error)})
        raise SystemExit(1)
