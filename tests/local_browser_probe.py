"""Real official CLI navigation, PNG, per-session idle reclamation; no services mocked."""
import concurrent.futures
import os
from pathlib import Path
import subprocess
import time
import http.server
import sys
import threading

class Fixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        content = b'<html><head><title>Agent Runtime fixture</title></head><body>local browser fixture</body></html>'
        self.send_response(200)
        self.send_header('Content-Type','text/html')
        self.end_headers()
        self.wfile.write(content)
    def log_message(self, *args): pass

server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = 'http://127.0.0.1:' + str(server.server_port) + '/'

assert os.getuid() == 1000
assert Path('/usr/bin/chromium').is_file(), 'Image must bundle Chromium'

def cli(session, *args):
    result = subprocess.run(['playwright-cli', '-s=' + session, *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '### Error' not in result.stdout, result.stdout
    return result.stdout

def chromium_pids():
    found = set()
    for p in Path('/proc').glob('[0-9]*/status'):
        try:
            fields = p.read_text().splitlines()
            name = fields[0].split()[1]
            state = next(l for l in fields if l.startswith('State:'))
            if name in ('chromium', 'chrome_crashpad') and 'Z' not in state:
                found.add(int(p.parent.name))
        except (OSError, StopIteration):
            pass
    return found

def await_gone(pids):
    for _ in range(100):
        if not (pids & chromium_pids()): return
        time.sleep(.1)
    raise AssertionError('Session Chromium processes survived idle deadline: ' + str(pids & chromium_pids()))

if len(sys.argv) > 1:
    mode = sys.argv[1]
    state = Path.home() / '.local/share/browser-smoke-state.json'
    try:
        cli('persist', 'open', url, '--idle-timeout=30000')
        if mode == 'persist-write':
            assert 'runtime-cookie=persisted' in cli('persist', 'eval', "() => { document.cookie='runtime-cookie=persisted; Max-Age=86400; Path=/'; return document.cookie; }")
            cli('persist', 'state-save', str(state))
            assert state.is_file()
        elif mode == 'persist-check':
            assert state.is_file(), 'Browser state did not persist across container recreation'
            cli('persist', 'state-load', str(state))
            cli('persist', 'goto', url)
            assert 'runtime-cookie=persisted' in cli('persist', 'eval', 'document.cookie')
        elif mode == 'isolated':
            assert not state.exists(), 'Other tenant can read saved browser state'
            assert 'runtime-cookie=persisted' not in cli('persist', 'eval', 'document.cookie')
        else: raise AssertionError('Unknown probe mode')
        cli('persist','close')
        assert not chromium_pids()
        print('PASS real Chromium native state-save/load cookie persistence/isolation:', mode)
    finally:
        subprocess.run(['playwright-cli','-s=persist','close'], capture_output=True, timeout=30)
        server.shutdown()
    raise SystemExit(0)

baseline = chromium_pids()
assert not baseline, baseline
try:
    # No --browser, --config or executable flag: exercise native HOME defaults.
    cli('idle-a', 'open', url, '--idle-timeout=4000')
    assert 'Agent Runtime fixture' in cli('idle-a', 'eval', 'document.title')
    a = chromium_pids()
    assert a
    cli('idle-b', 'open', 'about:blank', '--idle-timeout=30000')
    b = chromium_pids() - a
    assert b
    with concurrent.futures.ThreadPoolExecutor() as pool:
        results = list(pool.map(lambda s: cli(s, 'eval', "() => { document.body.innerHTML='<h1>runtime-'+" + repr(s) + "+'</h1>'; return document.body.innerText; }"), ['idle-a','idle-b']))
    assert 'runtime-idle-a' in results[0] and 'runtime-idle-b' in results[1], results
    screenshot = Path.home() / 'workspace/browser-smoke.png'
    cli('idle-a', 'screenshot', '--filename=' + str(screenshot))
    assert screenshot.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    await_gone(a)
    assert b <= chromium_pids(), 'Idle A killed B'
    assert 'runtime-idle-b' in cli('idle-b', 'eval', 'document.body.innerText')
    cli('idle-b', 'close')
    await_gone(b)
    assert not chromium_pids()
    print('PASS native defaults, concurrent named sessions, navigation/eval/PNG, idle A reclaimed without closing B, scoped close B, zero live Chromium')
finally:
    # Never close-all: tests only own these names.
    for s in ['idle-a','idle-b']:
        subprocess.run(['playwright-cli','-s='+s,'close'], capture_output=True, timeout=30)
