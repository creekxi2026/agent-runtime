"""Owned bind-HOME Compose smoke runner; resolves config without Compose resources.
Ledger is saved immediately after docker create, before docker start can fail.
Only sequential calls from smoke.sh are supported; each smoke invocation has its
own unique temporary ledger. No project-wide teardown or implicit Docker pull.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

from smoke_owned_resources import Resources, container_args


def docker(args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=180)
    if result.returncode: raise RuntimeError(result.stderr or result.stdout)
    return result.stdout


def main():
    mode = sys.argv[1]
    resources = Resources(docker)
    root = resources.create_fixture() if mode == 'init' else Path(sys.argv[2])
    ledger = root / 'owned-resources.json'
    if mode != 'init':
        state = json.loads(ledger.read_text())
        import re
        if not re.fullmatch(r'runtime-smoke-[0-9a-f]{32}', state['owner']):
            raise RuntimeError('Invalid fixture owner UUID')
        resources.owner = state['owner']
        resources.containers = state['containers']
        resources.fixtures = state['fixtures']
        resources.validate_fixture(root)
    def save():
        temporary = ledger.with_suffix('.tmp')
        temporary.write_text(json.dumps({'owner': resources.owner, 'containers': resources.containers,
                                         'fixtures': resources.fixtures}))
        os.replace(temporary, ledger)
    resources.save = save
    if mode == 'init':
        save()
        print(root)
        return
    if mode == 'cleanup':
        resources.cleanup()
        save()
        resources.remove_fixture_tree(root, os.environ['IMAGE'])
        assert not root.exists()
        assert not docker(['ps', '-aq', '--filter', 'label=io.creek.runtime.smoke-owner=' + resources.owner]).strip()
        print('CLEANUP_VERIFIED', resources.owner, 'containers=0 fixture_absent=true networks_created=0')
        return
    if mode == 'docker-run':
        raw = sys.argv[3:]
        if '--rm' in raw: raw.remove('--rm')
        cid = resources.create_container(['--pull=never', *raw])
        save()
        result = subprocess.run(['docker', 'start', '-ai', cid], timeout=180)
        resources.remove_container(cid)
        save()
        sys.exit(result.returncode)
    if mode != 'run': raise ValueError('Unsupported operation')
    project, shared = sys.argv[3:5]
    run_args = sys.argv[5:]
    if not run_args or run_args.pop(0) != 'run': raise ValueError('Only compose run supported')
    detached = '-d' in run_args
    while run_args and run_args[0].startswith('-'):
        option = run_args.pop(0)
        if option not in ('--rm', '-T', '--no-deps', '-d'):
            raise ValueError('Unsupported compose run flag: ' + option)
    if not run_args or run_args.pop(0) != 'runtime': raise ValueError('Only runtime supported')
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'IMAGE', 'SHARED_CODEX_DIR') if k in os.environ}
    env.update(HOME_DIR=str(root / (project + '-home')), COMPOSE_PROJECT_NAME=project)
    files = ['compose.yaml', 'compose.bind-home.yaml', 'tests/compose.yaml']
    if shared == 'true': files.append('compose.shared-codex.yaml')
    config = json.loads(subprocess.check_output(['docker', 'compose', '--env-file', 'runtime.env.example',
                       *sum((['-f', f] for f in files), []), '-p', project, 'config', '--format', 'json'], env=env, text=True))
    runtime = config['services']['runtime']
    # Fail closed: this runner cannot use pre-existing named HOME/cache storage.
    for mount in runtime['volumes']:
        if mount['type'] != 'bind': raise ValueError('Only task-owned bind fixtures supported')
        path = Path(mount['source']).resolve()
        if not path.is_relative_to(root.resolve()): raise ValueError('Mount outside task fixture')
        path.mkdir(parents=True, exist_ok=True)
    cid = resources.create_container(['-i', *container_args(runtime, run_args)])
    save()
    if detached:
        docker(['start', cid])
        print(cid)
    else:
        result = subprocess.run(['docker', 'start', '-ai', cid], timeout=180)
        resources.remove_container(cid)
        save()
        sys.exit(result.returncode)


if __name__ == '__main__': main()
