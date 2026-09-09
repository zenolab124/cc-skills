"""SSH/SCP transport detached from macOS app process trees for Loon LAN access."""
from __future__ import annotations
import base64, json, os, signal, subprocess, sys, tempfile, time, uuid
from pathlib import Path
from typing import Any

def transport_worker(path: Path) -> int:
    """One-shot launchd child, detached from the app's Loon TUN process tree."""
    request = json.loads(path.read_text())
    marker = path.parent / 'started'
    try:
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        return 0
    result: dict[str, Any]
    process = subprocess.Popen(request['argv'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace', start_new_session=True)
    def stop(_signum: int, _frame: Any) -> None:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        raise SystemExit(130)
    signal.signal(signal.SIGTERM, stop)
    try:
        stdout, stderr = process.communicate(timeout=request['timeout'])
        result = {'returncode': process.returncode, 'stdout': stdout, 'stderr': stderr}
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        result = {'returncode': 124, 'stdout': stdout, 'stderr': stderr + '\ntransport timeout'}
    temporary = path.parent / 'result.tmp'
    temporary.write_text(json.dumps(result), encoding='utf-8')
    temporary.replace(path.parent / 'result.json')
    return 0


def transport(argv: list[str], timeout: int = 45) -> dict[str, Any]:
    if sys.platform != 'darwin':
        p = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
        return {'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    with tempfile.TemporaryDirectory(prefix='windows-remote-transport-') as tmp:
        folder = Path(tmp); request = folder / 'request.json'
        request.write_text(json.dumps({'argv': argv, 'timeout': timeout}), encoding='utf-8')
        label = 'codex.windows.remote.' + uuid.uuid4().hex
        try:
            subprocess.run(['/bin/launchctl', 'submit', '-l', label, '--', sys.executable,
                            str(Path(__file__).resolve()), '_transport', str(request)],
                           check=True, capture_output=True)
            deadline = time.monotonic() + timeout + 8
            result = folder / 'result.json'
            while not result.exists():
                if time.monotonic() >= deadline: raise TimeoutError('launchd transport did not return')
                time.sleep(0.2)
            return json.loads(result.read_text())
        finally:
            subprocess.run(['/bin/launchctl', 'remove', label], capture_output=True)


def check_transport(result: dict[str, Any]) -> str:
    if result['returncode']:
        raise RuntimeError(f"remote transport exited {result['returncode']}: {result['stderr'][-1600:]}")
    return result['stdout'].strip()


def ssh(host: str, script: str, timeout: int = 45) -> dict[str, Any]:
    prefix = "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
    encoded = base64.b64encode((prefix + script).encode('utf-16le')).decode()
    return transport(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=6', host,
        'powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ' + encoded], timeout)


def scp(host: str, local: Path, remote: str, *, download: bool = False) -> None:
    if any(x in remote for x in ['\n', '\r', '\0']): raise ValueError('invalid remote path')
    pair = [host + ':' + remote.replace('\\', '/'), str(local)] if download else [str(local), host + ':' + remote.replace('\\', '/')]
    check_transport(transport(['/usr/bin/scp', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                               '-o', 'ConnectTimeout=6', *pair], timeout=150))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '_transport':
        raise SystemExit(transport_worker(Path(sys.argv[2])))
