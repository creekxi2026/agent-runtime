"""Compose download-cache contract (no credentials or daemon startup)."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def config(*files):
    env = dict(os.environ, COMPOSE_PROJECT_NAME='cache-contract')
    result = subprocess.run(['docker', 'compose', '--env-file', '/dev/null',
                             *sum((['-f', str(ROOT / f)] for f in files), []),
                             'config', '--format', 'json'], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)['services']


class SharedCacheTests(unittest.TestCase):
    def test_optional_override_preserves_single_service_and_image_command(self):
        self.assertTrue((ROOT / 'compose.shared-cache.yaml').exists(),
                        'Optional download cache override is missing')
        base = config('compose.yaml')['runtime']
        services = config('compose.yaml', 'compose.shared-cache.yaml')
        self.assertEqual(list(services), ['runtime'])
        runtime = services['runtime']
        self.assertEqual(runtime['image'], base['image'])
        self.assertEqual(runtime['cap_add'], base['cap_add'])
        self.assertEqual(runtime['security_opt'], base['security_opt'])
        self.assertEqual(runtime['command'], ['multica', 'daemon', 'start', '--foreground',
                         '--no-auto-update', '--no-auto-reload', '--workspaces-root', '/home/agent/workspace'])
        for key, value in {'NPM_CONFIG_CACHE': '/shared/caches/npm',
                           'GOMODCACHE': '/shared/caches/go-mod',
                           'UV_CACHE_DIR': '/shared/caches/uv', 'UV_LINK_MODE': 'copy'}.items():
            self.assertEqual(runtime['environment'][key], value)
        mounts = {m['target']: m for m in runtime['volumes']}
        self.assertEqual(set(mounts), {'/home/agent', '/shared/caches'})
        self.assertFalse(mounts['/shared/caches'].get('read_only', False))
        self.assertNotIn('NPM_CONFIG_CACHE', base['environment'])
        self.assertIn('exec /usr/local/bin/agent-bootstrap "$$@"', runtime['entrypoint'][2])


@unittest.skipUnless(os.environ.get('SHARED_CACHE_DOCKER_TEST') == '1', 'opt-in real Docker permission tests')
class CachePermissionTests(unittest.TestCase):
    def test_symlink_rejected_before_any_permission_change(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='cache-symlink-') as tmp:
            root = Path(tmp)
            cache = root / 'cache'
            cache.mkdir()
            npm = cache / 'npm'
            npm.mkdir(mode=0o700)
            before = npm.stat()
            outside = root / 'outside'
            outside.mkdir(mode=0o700)
            (cache / 'uv').symlink_to('/outside')
            env = dict(os.environ, COMPOSE_PROJECT_NAME='cache-permission', HOME_DIR=str(root / 'home'), SHARED_CACHE_DIR=str(cache))
            result = subprocess.run(['docker', 'compose', '--env-file', '/dev/null', '-f', str(ROOT / 'compose.yaml'), '-f', str(ROOT / 'compose.shared-cache.yaml'), '-f', str(ROOT / 'compose.bind-home.yaml'), '-f', str(ROOT / 'compose.shared-cache.bind.yaml'), 'run', '--rm', '--no-deps', '-v', str(outside) + ':/outside', 'runtime', 'true'], env=env, capture_output=True, text=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(npm.stat().st_mode, before.st_mode)
            self.assertEqual(outside.stat().st_mode & 0o777, 0o700)
            self.assertFalse((cache / 'go-mod').exists())

    def test_fresh_cache_init_is_nonrecursive_and_drops_capabilities(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='cache-permission-') as tmp:
            root = Path(tmp)
            cache = root / 'cache'
            cache.mkdir()
            (cache / 'sentinel').write_text('untouched')
            original = (cache / 'sentinel').stat()
            env = dict(os.environ, COMPOSE_PROJECT_NAME='cache-permission', HOME_DIR=str(root / 'home'), SHARED_CACHE_DIR=str(cache))
            args = ['docker', 'compose', '--env-file', '/dev/null', '-f', str(ROOT / 'compose.yaml'), '-f', str(ROOT / 'compose.shared-cache.yaml'), '-f', str(ROOT / 'compose.bind-home.yaml'), '-f', str(ROOT / 'compose.shared-cache.bind.yaml'), 'run', '--rm', '--no-deps', 'runtime']
            probe = '''set -eu; test "$(id -u)" = 1000; for d in npm go-mod uv; do test -w /shared/caches/$d; test "$(stat -c %a /shared/caches/$d)" = 750; done; test "$(awk '/CapEff/ {print $2}' /proc/self/status)" = 0000000000000000; test "$(awk '/NoNewPrivs/ {print $2}' /proc/self/status)" = 1'''
            for _ in range(2):
                result = subprocess.run(args + ['sh', '-ec', probe], env=env, text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            after = (cache / 'sentinel').stat()
            self.assertEqual((after.st_uid, after.st_mode), (original.st_uid, original.st_mode))
            self.assertEqual(set(p.name for p in cache.iterdir()), {'npm', 'go-mod', 'uv', 'sentinel'})


if __name__ == '__main__':
    unittest.main()
