"""Disposable Linux UID mismatch regression using an already-local image only."""
import json
import os
from pathlib import Path
import subprocess

from smoke_owned_resources import Resources, container_args, FIXTURE_CLEANUP_SCRIPT


def docker(args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout + (result.stderr if args[0] == 'logs' else '')


def main():
    image = json.loads(docker(['image', 'inspect', os.environ.get(
        'IMAGE', 'ghcr.io/creekxi2026/agent-runtime:latest')]))[0]['Id']
    resources = Resources(docker)
    root = resources.create_fixture()
    # A separate, invocation-owned sentinel is deliberately NOT mounted by cleanup.
    sentinel = resources.create_fixture()
    sentinel.chmod(0o700)
    (sentinel / 'keep').write_text('unchanged')
    before = sentinel.stat()
    root.chmod(0o777)
    def save():
        (root / 'owned-resources.json').write_text(json.dumps({
            'owner': resources.owner, 'containers': resources.containers,
            'fixtures': resources.fixtures}))
    resources.save = save
    save()
    try:
        def probe(uid, script):
            cid = resources.start_container(container_args({
                'image': image, 'user': str(uid) + ':' + str(uid),
                'network_mode': 'none', 'entrypoint': ['python3'],
                'cap_drop': ['ALL'],
                'cap_add': ['CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID'] if uid == 0 else [],
                'tmpfs': ['/tmp'],
                'security_opt': ['no-new-privileges:true'],
                'read_only': True,
                'volumes': [{'type': 'bind', 'source': str(root), 'target': '/fixture'}],
            }, ['-c', script]))
            save()
            status = docker(['wait', cid]).strip()
            output = docker(['logs', cid])
            resources.remove_container(cid)
            save()
            assert status == '0', output
            return output
        probe(1000, '''import os
assert os.getuid() == 1000
os.mkdir('/fixture/readable', 0o755)
os.mkdir('/fixture/locked', 0o700)
open('/fixture/locked/keep', 'w').write('container-owned')
os.mkdir('/fixture/locked/nested', 0o700)
open('/fixture/locked/nested/file', 'w').write('nested')
os.chmod('/fixture/readable', 0o555)
os.chmod('/fixture/locked', 0o000)
''')
        (root / 'foreign-link').symlink_to(sentinel, target_is_directory=True)
        (root / 'dangling-link').symlink_to('/missing/foreign')
        # VM bind sharing remaps ownership to the calling UID. Use native Linux
        # tmpfs for genuine UID mismatch, executing the exact cleanup payload.
        output = probe(0, '''import os
os.mkdir('/tmp/native', 0o755)
os.mkdir('/tmp/native/readable', 0o755)
os.mkdir('/tmp/native/locked', 0o700)
os.mkdir('/tmp/native/locked/nested', 0o700)
open('/tmp/native/locked/nested/file', 'w').write('fixture')
for path in ('/tmp/native/readable', '/tmp/native/locked'):
    os.chown(path, 1000, 1000)
os.chmod('/tmp/native/readable', 0o555)
os.chmod('/tmp/native/locked', 0o000)
os.mkdir('/tmp/foreign', 0o700)
open('/tmp/foreign/keep', 'w').write('foreign')
os.symlink('/tmp/foreign', '/tmp/native/link')
os.seteuid(2001)
assert os.geteuid() == 2001
assert os.stat('/tmp/native/readable').st_uid == 1000
try:
    os.chmod('/tmp/native/readable', 0o755)
except PermissionError:
    print('RED_REPRODUCED: host UID2001 cannot chmod UID1000 directory')
else:
    raise AssertionError('Mismatch fixture did not reproduce host chmod failure')
try:
    os.listdir('/tmp/native/locked')
except PermissionError:
    print('RED_REPRODUCED: non-traversable UID1000 directory')
else:
    raise AssertionError('Expected non-traversable directory')
os.seteuid(0)
''' + '\nexec(' + repr(FIXTURE_CLEANUP_SCRIPT.replace('/fixture', '/tmp/native')) + ')\n' + '''
assert not os.listdir('/tmp/native')
assert open('/tmp/foreign/keep').read() == 'foreign'
assert os.stat('/tmp/foreign').st_mode & 0o777 == 0o700
print('GREEN: exact cleanup payload removed native UID mismatch tree')
''')
        print(output, end='')
    finally:
        resources.cleanup()
        resources.remove_fixture_tree(root, image)
        assert not root.exists()
        assert (sentinel / 'keep').read_text() == 'unchanged'
        assert sentinel.stat().st_mode == before.st_mode
        assert sentinel.stat().st_uid == before.st_uid
        # Cleanup this second allocated tree without recreating the first ledger.
        resources.save = lambda: None
        resources.remove_fixture_tree(sentinel, image)
        assert not sentinel.exists()
        assert not docker(['ps', '-aq', '--filter',
                           'label=io.creek.runtime.smoke-owner=' + resources.owner]).strip()
        assert not docker(['volume', 'ls', '-q', '--filter',
                           'label=io.creek.runtime.smoke-owner=' + resources.owner]).strip()
    print('PASS UID1000/UID2001, mode000 nested directories, symlink sentinel preserved')
    print('CLEANUP_VERIFIED', resources.owner,
          'containers=0 volumes=0 fixtures_absent=2 networks_created=0')


if __name__ == '__main__':
    main()
