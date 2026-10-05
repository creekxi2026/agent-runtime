"""Task-local Docker ownership ledger. Never delete resources by an inferred name."""
import json
import os
import shutil
import stat
import re
import uuid

LABEL = 'io.creek.runtime.smoke-owner'

FIXTURE_CLEANUP_SCRIPT = '''import os, stat

def clear(fd):
    for name in os.listdir(fd):
        if name == 'owned-resources.json' and fd == rootfd:
            continue  # Keep the host recovery ledger until helper removal.
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            pathfd = os.open(name, getattr(os, 'O_PATH', os.O_RDONLY) |
                             os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                if hasattr(os, 'O_PATH'):
                    os.chmod('/proc/self/fd/' + str(pathfd), stat.S_IMODE(info.st_mode) | 0o700)
                else:
                    os.fchmod(pathfd, stat.S_IMODE(info.st_mode) | 0o700)
                childfd = os.open('.', os.O_RDONLY | os.O_DIRECTORY, dir_fd=pathfd)
                try:
                    clear(childfd)
                finally:
                    os.close(childfd)
            finally:
                os.close(pathfd)
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)

rootfd = os.open('/fixture', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    clear(rootfd)
finally:
    os.close(rootfd)
'''


def container_args(config, command):
    """Use resolved Compose runtime settings, but no implicit network/volume creation."""
    args = ['--pull=never', '--network', config.get('network_mode', 'bridge')]
    for key, flag in [('user', '--user'), ('working_dir', '--workdir')]:
        if config.get(key): args += [flag, config[key]]
    for key, value in config.get('environment', {}).items():
        if value is not None: args += ['--env', key + '=' + str(value)]
    for mount in config.get('volumes', []):
        value = 'type={type},src={source},dst={target}'.format(**mount)
        if mount.get('read_only'): value += ',readonly'
        if mount.get('volume', {}).get('nocopy'): value += ',volume-nocopy'
        args += ['--mount', value]
    for key, flag in [('cap_drop', '--cap-drop'), ('cap_add', '--cap-add'),
                      ('security_opt', '--security-opt'), ('tmpfs', '--tmpfs')]:
        for value in config.get(key, []): args += [flag, value]
    if config.get('read_only'): args += ['--read-only']
    entrypoint = [value.replace('$$', '$') for value in (config.get('entrypoint') or [])]
    if entrypoint: args += ['--entrypoint', entrypoint[0]]
    return args + [config['image']] + entrypoint[1:] + command


def cache_fixture_args(image, fixture):
    """Keep the fixture on loopback in a task-owned, egress-disabled namespace.

    Clients join this container's namespace; no host ports or host DNS needed.
    Keep sleep alive after stopping HTTP so offline recreation can still join.
    """
    return container_args({
        'image': image, 'network_mode': 'none', 'user': '1000:1000',
        'entrypoint': ['/bin/sh'], 'read_only': True, 'tmpfs': ['/tmp'],
        'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        'volumes': [{'type': 'bind', 'source': str(fixture),
                     'target': '/fixtures', 'read_only': True}],
    }, ['-ec', 'python3 -m http.server 8765 --bind 127.0.0.1 --directory /fixtures & '
               'echo $! > /tmp/fixture.pid; exec sleep 600'])


class Resources:
    def __init__(self, docker):
        self.docker = docker
        self.owner = 'runtime-smoke-' + uuid.uuid4().hex
        self.containers = []
        self.volumes = []
        self.fixtures = {}
        self.save = lambda: None

    def create_fixture(self, parent=None):
        """Allocate, rather than adopt, the only host roots cleanup may mount."""
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp(prefix=self.owner + '-', dir=parent)).resolve()
        info = root.lstat()
        self.fixtures[str(root)] = [info.st_dev, info.st_ino]
        self.save()
        return root

    def validate_fixture(self, root):
        from pathlib import Path
        root = Path(root).absolute()
        info = root.lstat()
        if (not re.fullmatch(r'runtime-smoke-[0-9a-f]{32}', self.owner)
                or not root.name.startswith(self.owner + '-')
                or str(root) not in self.fixtures or root.resolve() != root
                or not stat.S_ISDIR(info.st_mode)
                or self.fixtures[str(root)] != [info.st_dev, info.st_ino]):
            raise RuntimeError('Unrecorded or replaced fixture root')
        return root

    def remove_fixture_tree(self, root, image):
        root = self.validate_fixture(root)
        if self.containers or self.volumes:
            raise RuntimeError('Stop owned resources before fixture cleanup')
        # fd-relative traversal never follows symlinks. FOWNER repairs only
        # opened directories (also needed by virtiofs); DAC_OVERRIDE handles
        # native Linux's non-traversable, differently-owned cache directories.
        script = FIXTURE_CLEANUP_SCRIPT
        cid = self.create_container(container_args({
            'image': image, 'network_mode': 'none', 'user': '0:0',
            'entrypoint': ['python3'], 'read_only': True,
            'cap_drop': ['ALL'], 'cap_add': ['DAC_OVERRIDE', 'FOWNER'],
            'security_opt': ['no-new-privileges:true'],
            'volumes': [{'type': 'bind', 'source': str(root), 'target': '/fixture'}],
        }, ['-c', script]))
        self.save()  # Persist the helper ID before starting it can fail.
        self.docker(['start', cid])
        status = self.docker(['wait', cid]).strip()
        if status != '0':
            raise RuntimeError('Fixture cleanup helper failed: ' + status)
        self.remove_container(cid)
        self.save()
        (root / 'owned-resources.json').unlink(missing_ok=True)
        root.rmdir()
        del self.fixtures[str(root)]

    def create_volume(self, suffix):
        name = self.owner + '-' + suffix
        if self.docker(['volume', 'ls', '--filter', 'name=^' + name + '$', '-q']).strip():
            raise RuntimeError('Volume already exists: ' + name)
        created = self.docker(['volume', 'create', '--label', LABEL + '=' + self.owner, name]).strip()
        info = json.loads(self.docker(['volume', 'inspect', created]))[0]
        if created != name or info['Name'] != name or info.get('Labels', {}).get(LABEL) != self.owner:
            raise RuntimeError('Volume ownership mismatch: ' + name)
        self.volumes.append(name)
        return name

    def create_container(self, args):
        cid = self.docker(['create', '--name', self.owner + '-' + uuid.uuid4().hex,
                           '--label', LABEL + '=' + self.owner, *args]).strip()
        if not re.fullmatch('[0-9a-f]{64}', cid):
            raise RuntimeError('Docker did not return a full container ID')
        self.containers.append(cid)
        return cid

    def start_container(self, args):
        cid = self.create_container(args)
        self.docker(['start', cid])
        return cid

    def remove_container(self, cid):
        if cid not in self.containers:
            raise RuntimeError('Unrecorded container')
        info = json.loads(self.docker(['inspect', cid]))[0]
        if info['Id'] != cid or info['Config'].get('Labels', {}).get(LABEL) != self.owner:
            raise RuntimeError('Container ownership mismatch: ' + cid)
        self.docker(['rm', '-f', cid])
        self.containers.remove(cid)

    def cleanup(self):
        errors = []
        for cid in self.containers[:]:
            try: self.remove_container(cid)
            except Exception as exc: errors.append(str(exc))
        for name in self.volumes[:]:
            try:
                info = json.loads(self.docker(['volume', 'inspect', name]))[0]
                if info['Name'] != name or info.get('Labels', {}).get(LABEL) != self.owner:
                    raise RuntimeError('Volume ownership mismatch: ' + name)
                self.docker(['volume', 'rm', name])
                self.volumes.remove(name)
            except Exception as exc: errors.append(str(exc))
        if errors: raise RuntimeError('; '.join(errors))
