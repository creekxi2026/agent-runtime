"""Real published-image download/cache smoke; no credentials, build or daemon.
Run from repository root: python3 tests/shared_cache_smoke.py
"""
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

from smoke_owned_resources import Resources, container_args, cache_fixture_args

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get('SHARED_CACHE_IMAGE', 'ghcr.io/creekxi2026/agent-runtime:latest')
MODE = os.environ.get('SHARED_CACHE_STORAGE', 'bind')
if MODE not in ('bind', 'named'):
    raise ValueError('SHARED_CACHE_STORAGE must be bind or named')
resources = Resources(lambda args: run(['docker', *args]))
LOG = ROOT / '.build-inputs/shared-cache' / (resources.owner + '-' + MODE + '.log')
LOG.parent.mkdir(parents=True, exist_ok=True)
log = LOG.open('w')


def run(args, env=None):
    result = subprocess.run(args, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    log.write('$ ' + ' '.join(args) + '\n' + result.stdout + '\n'); log.flush()
    if result.returncode:
        raise RuntimeError(f'{args[:4]} exited {result.returncode}: {result.stdout}')
    return result.stdout


def fixtures(root):
    with tarfile.open(root / 'fixture.tgz', 'w:gz') as archive:
        for name, data in {'package/package.json': '{"name":"cache-fixture","version":"1.0.0","main":"index.js"}', 'package/index.js': 'module.exports = "fixture";\n'}.items():
            data = data.encode(); info = tarfile.TarInfo(name); info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    proxy = root / 'go/example.test/cachefixture/@v'; proxy.mkdir(parents=True)
    mod = 'module example.test/cachefixture\n\ngo 1.20\n'
    (proxy / 'v1.0.0.mod').write_text(mod)
    (proxy / 'v1.0.0.info').write_text('{"Version":"v1.0.0","Time":"2024-01-01T00:00:00Z"}')
    (proxy / 'list').write_text('v1.0.0\n')
    with zipfile.ZipFile(proxy / 'v1.0.0.zip', 'w') as z:
        z.writestr('example.test/cachefixture@v1.0.0/go.mod', mod)
        z.writestr('example.test/cachefixture@v1.0.0/fixture.go', 'package cachefixture\nfunc Value() string { return "fixture" }\n')
    wheel = root / 'cache_fixture-1.0.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as z:
        z.writestr('cache_fixture/__init__.py', 'VALUE = "fixture"\n')
        z.writestr('cache_fixture-1.0.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: cache-fixture\nVersion: 1.0.0\n')
        z.writestr('cache_fixture-1.0.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        z.writestr('cache_fixture-1.0.0.dist-info/RECORD', '')
    index = root / 'simple/cache-fixture'; index.mkdir(parents=True)
    index.joinpath('index.html').write_text(f'<a href="../../{wheel.name}#sha256={hashlib.sha256(wheel.read_bytes()).hexdigest()}">{wheel.name}</a>')


names = [None, None]
server = None
# Only the mkdtemp root allocated by this ownership ledger may be cleaned.
temporary = resources.create_fixture()
def save_ledger():
    (temporary / 'owned-resources.json').write_text(json.dumps({
        'owner': resources.owner, 'containers': resources.containers,
        'volumes': resources.volumes, 'fixtures': resources.fixtures}))
resources.save = save_ledger
save_ledger()
try:
    with contextlib.nullcontext(temporary) as tmp:
        root = Path(tmp); fixture = root / 'fixtures'; fixture.mkdir(); fixtures(fixture)
        cache = root / 'cache-$(touch INJECTION)'; cache.mkdir()
        root.chmod(0o755)
        url = 'http://127.0.0.1:8765'
        npm = fixture / 'npm'; npm.mkdir()
        npm.joinpath('cache-fixture').write_text(json.dumps({'name': 'cache-fixture', 'dist-tags': {'latest': '1.0.0'}, 'versions': {'1.0.0': {'name': 'cache-fixture', 'version': '1.0.0', 'dist': {'tarball': url + '/fixture.tgz', 'shasum': hashlib.sha1((fixture / 'fixture.tgz').read_bytes()).hexdigest()}}}}))
        image = json.loads(run(['docker', 'image', 'inspect', IMAGE]))[0]
        digest = image['Id']
        log.write('IMAGE_ID=' + digest + ' REPO_DIGESTS=' + json.dumps(image.get('RepoDigests', [])) + '\n'); log.flush()
        server = resources.start_container(cache_fixture_args(digest, fixture))
        run(['docker', 'exec', server, 'python3', '-c', '''import time, urllib.request
for attempt in range(100):
    try:
        urllib.request.urlopen("http://127.0.0.1:8765/fixture.tgz", timeout=1).read()
        break
    except OSError:
        time.sleep(0.1)
else:
    raise RuntimeError("Fixture HTTP server not ready")
'''])
        homes = [resources.create_volume('home-' + str(i)) for i in range(2)] if MODE == 'named' else []
        shared_volume = resources.create_volume('cache') if MODE == 'named' else ''
        def compose(i):
            # Do not inherit real tokens or external storage overrides from the caller.
            env = {k: os.environ[k] for k in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT') if k in os.environ}
            env.update(COMPOSE_PROJECT_NAME=resources.owner + '-' + str(i), HOME_DIR=str(root / f'home-{i}'), SHARED_CACHE_DIR=str(cache), RUNTIME_IMAGE=digest)
            files = ['compose.yaml', 'compose.shared-cache.yaml']
            if MODE == 'bind': files += ['compose.bind-home.yaml', 'compose.shared-cache.bind.yaml']
            else: env.update(HOME_VOLUME=homes[i], SHARED_CACHE_VOLUME=shared_volume)
            return ['docker', 'compose', '--env-file', '/dev/null', *sum((['-f', str(ROOT / f)] for f in files), [])], env
        def start(i):
            args, env = compose(i)
            config = json.loads(run(args + ['config', '--format', 'json'], env))
            runtime = config['services']['runtime']
            # Compose config names volume keys, not their resolved engine names.
            for mount in runtime['volumes']:
                if mount['type'] == 'volume': mount['source'] = config['volumes'][mount['source']]['name']
                elif mount['type'] == 'bind': Path(mount['source']).mkdir(parents=True, exist_ok=True)
            runtime['network_mode'] = 'container:' + server
            names[i] = resources.start_container(container_args(runtime, ['sh', '-ec', 'id; cat /proc/self/status; exec sleep 600']))
        for i in range(2): start(i)
        for name in names:
            run(['docker', 'exec', '--user', '1000:1000', name, 'sh', '-ec', 'test -w /shared/caches/npm; test -w /shared/caches/go-mod; test -w /shared/caches/uv'])
            output = run(['docker', 'logs', name])
            assert 'uid=1000' in output and 'CapEff:\t0000000000000000' in output and 'CapBnd:\t0000000000000000' in output and 'NoNewPrivs:\t1' in output
        run(['docker', 'exec', '--user', '1000:1000', names[0], 'sh', '-ec', 'printf tenant-a > "$HOME/private-home-marker"; printf private-session > "$CODEX_HOME/session-marker"'])
        run(['docker', 'exec', '--user', '1000:1000', names[1], 'sh', '-ec', 'test ! -e "$HOME/private-home-marker"; test ! -e "$CODEX_HOME/session-marker"'])
        def install(i, offline=False, project='first'):
            mode = '--offline' if offline else ''
            proxy = 'off' if offline else url + '/go'
            script = f'''set -eu
mkdir -p "$HOME/workspace/{project}"; cd "$HOME/workspace/{project}"
npm install --allow-remote=all --update-notifier=false --ignore-scripts --no-audit --no-fund {mode} --registry {url}/npm cache-fixture@1.0.0
node -e 'if(require("cache-fixture")!=="fixture")process.exit(1)'
printf 'module smoke\n\ngo 1.20\n\nrequire example.test/cachefixture v1.0.0\n' > go.mod
printf 'package main\nimport("fmt"; "example.test/cachefixture")\nfunc main() {{fmt.Println(cachefixture.Value())}}\n' > main.go
GOPROXY={proxy} GOSUMDB=off go mod download all
GOPROXY={proxy} GOSUMDB=off go build -o private-go .
test "$(./private-go)" = fixture
uv venv --python /usr/bin/python3 .venv
uv pip install --python .venv/bin/python {mode} --index-url {url}/simple cache-fixture==1.0.0
.venv/bin/python -c 'import cache_fixture; assert cache_fixture.VALUE == "fixture"'
echo 'PASS npm/go/uv {project} offline={offline}'
'''
            return run(['docker', 'exec', '--user', '1000:1000', names[i], 'sh', '-ec', script])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda i: install(i, False, 'cold-concurrent'), range(2)))
        install(0)
        run(['docker', 'exec', server, 'sh', '-ec', 'kill "$(cat /tmp/fixture.pid)"'])
        run(['docker', 'exec', server, 'python3', '-c', '''import socket, time
for attempt in range(100):
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 8765)) != 0:
            break
    time.sleep(0.1)
else:
    raise RuntimeError("Fixture HTTP server still reachable")
'''])
        install(1, True)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda i: install(i, True, 'concurrent'), range(2)))
        # Private mutable installations must not alias cache files or other HOME.
        for name in names:
            run(['docker', 'exec', '--user', '1000:1000', name, 'sh', '-ec', 'test "$(npm config get prefix)" = /home/agent/.local; test "$(go env GOCACHE)" = /home/agent/.cache/go-build; test "$(stat -c %h /home/agent/workspace/first/.venv/lib/python*/site-packages/cache_fixture/__init__.py)" = 1'])
        run(['docker', 'exec', '--user', '1000:1000', names[0], 'sh', '-ec', 'echo changed > workspace/first/node_modules/cache-fixture/index.js; for f in workspace/first/.venv/lib/python*/site-packages/cache_fixture/__init__.py; do echo changed > "$f"; done; echo changed > workspace/first/private-go'])
        run(['docker', 'exec', '--user', '1000:1000', names[1], 'sh', '-ec', 'cd workspace/first; node -e \'if(require("cache-fixture")!=="fixture")process.exit(1)\'; .venv/bin/python -c \'import cache_fixture; assert cache_fixture.VALUE == "fixture"\'; test "$(./private-go)" = fixture'])
        run(['docker', 'restart', *names])
        install(1, True, 'restart')
        for cid in names: resources.remove_container(cid)
        for i in range(2): start(i)
        run(['docker', 'exec', '--user', '1000:1000', names[0], 'sh', '-ec', 'test "$(cat "$HOME/private-home-marker")" = tenant-a; test "$(cat "$CODEX_HOME/session-marker")" = private-session'])
        run(['docker', 'exec', '--user', '1000:1000', names[1], 'sh', '-ec', 'test ! -e "$HOME/private-home-marker"; test ! -e "$CODEX_HOME/session-marker"'])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda i: install(i, True, 'recreated'), range(2)))
        assert not (ROOT / 'INJECTION').exists()
        log.write('PASS independent homes, offline reuse, concurrent reuse, restart/recreation, literal hostile cache path\n')
        print('PASS real npm/Go/uv shared download cache smoke; logs:', LOG)
finally:
    try:
        resources.cleanup()
        resources.remove_fixture_tree(temporary, IMAGE)
        assert not temporary.exists(), 'Owned fixture remains'
        remaining = run(['docker', 'ps', '-aq', '--filter', 'label=io.creek.runtime.smoke-owner=' + resources.owner]).strip()
        volumes = run(['docker', 'volume', 'ls', '-q', '--filter', 'label=io.creek.runtime.smoke-owner=' + resources.owner]).strip()
        assert not remaining and not volumes, 'Owned resources remain'
        log.write('CLEANUP_VERIFIED owner=' + resources.owner + ' containers=0 volumes=0 networks_created=0\n'); log.flush()
        print('CLEANUP_VERIFIED', resources.owner, 'containers=0 volumes=0 networks_created=0')
    finally:
        log.close()
