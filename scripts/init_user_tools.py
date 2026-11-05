#!/usr/bin/env python3
"""Seed private HOME once, offline, after bootstrap has dropped privileges."""
import fcntl
import json
import os
from pathlib import Path
import platform
import shutil


def exists(path):
    return path.exists() or path.is_symlink()


def private_directory(path, home):
    """Never follow a user link outside the private installation tree."""
    relative = path.relative_to(home)
    current = home
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeError('Refusing linked installation directory: ' + str(current))
        current.mkdir(exist_ok=True)


def save(path, data):
    temporary = path.with_suffix('.tmp')
    if temporary.is_symlink():
        raise RuntimeError('Refusing linked state file')
    temporary.write_text(json.dumps(data, sort_keys=True) + '\n')
    os.replace(temporary, path)


def npm_tool(source, name):
    package = json.loads((source / 'package.json').read_text())
    bins = package.get('bin', {})
    if isinstance(bins, str):
        bins = {name.rsplit('/', 1)[-1]: bins}
    for command, relative in bins.items():
        if '/' in command or command in ('', '.', '..'):
            raise ValueError('Invalid command name')
        (source / relative).resolve(strict=True).relative_to(source.resolve())
    return {'id': 'npm:' + name, 'source': source,
            'target': Path('.local/lib/node_modules') / name,
            'bins': bins, 'version': package['version']}


def seed(home, tools, architecture=None):
    home = home.resolve()
    state_dir = home / '.local/share/agent-runtime'
    private_directory(state_dir, home)
    private_directory(home / '.local/bin', home)
    lock_path = state_dir / 'initialize.lock'
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_path = state_dir / 'tools.json'
        if state_path.is_symlink():
            raise RuntimeError('Refusing linked tool state')
        arch = architecture or platform.machine()
        state = json.loads(state_path.read_text()) if state_path.exists() else {'arch': arch, 'tools': {}}
        if state['arch'] != arch:
            raise RuntimeError('HOME tools belong to a different architecture; migrate explicitly')
        for tool in tools:
            identity = tool['id']
            prior = state['tools'].get(identity)
            if prior and prior['status'] in ('installed', 'preserved'):
                # Deliberate removals and user upgrades must survive recreation.
                continue
            target = home / tool['target']
            target.relative_to(home)
            private_directory(target.parent, home)
            links = {home / '.local/bin' / name: target / relative
                     for name, relative in tool.get('bins', {}).items()}
            if prior is None and (exists(target) or any(exists(link) for link in links)):
                state['tools'][identity] = {'status': 'preserved'}
                save(state_path, state)
                print('Preserved existing user tool:', identity, flush=True)
                continue
            if prior is None:
                state['tools'][identity] = {'status': 'pending', 'version': tool['version']}
                save(state_path, state)
            # A fixed private transaction directory makes interrupted copies recoverable.
            stage = state_dir / 'staging'
            if stage.is_symlink():
                raise RuntimeError('Refusing linked tool staging directory')
            if stage.exists():
                shutil.rmtree(stage)
            if not exists(target):
                stage.mkdir(mode=0o700)
                payload = stage / 'payload'
                if tool['source'].is_dir():
                    shutil.copytree(tool['source'], payload, symlinks=True)
                else:
                    shutil.copy2(tool['source'], payload)
                os.rename(payload, target)
                stage.rmdir()
            elif target.is_symlink():
                raise RuntimeError('Interrupted installation target is a symlink: ' + str(target))
            for link, destination in links.items():
                relative = os.path.relpath(destination, link.parent)
                if exists(link):
                    if not link.is_symlink() or os.readlink(link) != relative:
                        raise RuntimeError('Command conflicts with interrupted installation: ' + str(link))
                else:
                    link.symlink_to(relative)
            state['tools'][identity] = {'status': 'installed', 'seed_version': tool['version']}
            save(state_path, state)


def main():
    if os.getuid() != 1000:
        raise RuntimeError('Tool initialization requires application UID 1000')
    manifest = json.loads(Path('/usr/share/agent-runtime/dependencies.json').read_text())
    tools = []
    for name in sorted(manifest['npm']):
        if name == 'npm':
            continue
        prefix = Path('/opt/codex' if name == '@openai/codex' else '/opt/agent-tools')
        tools.append(npm_tool(prefix / 'node_modules' / name, name))
    for name in ('multica', 'gh', 'uv', 'uvx'):
        source = Path('/opt/multica/bin/multica' if name == 'multica' else '/usr/local/bin/' + name)
        tools.append({'id': 'binary:' + name, 'source': source,
                      'target': Path('.local/bin') / name,
                      'version': manifest['versions']['uv' if name == 'uvx' else name]})
    seed(Path(os.environ['HOME']), tools)


if __name__ == '__main__':
    main()
