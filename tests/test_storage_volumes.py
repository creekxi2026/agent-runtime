"""Named-volume deployment and explicit bind compatibility contracts."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

def resolve_template(project, **values):
    env = {k: os.environ[k] for k in ('PATH', 'HOME') if k in os.environ}
    env.update(COMPOSE_PROJECT_NAME=project, **values)
    result = subprocess.run(
        ['docker', 'compose', '--project-directory', str(ROOT), '--env-file',
         str(ROOT / 'runtime.env.example'), 'config', '--format', 'json'],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)

def resolve(project='storage-a', files=(), **values):
    env = {k: os.environ[k] for k in ('PATH', 'HOME') if k in os.environ}
    env.update(COMPOSE_PROJECT_NAME=project, **values)
    args = ['docker', 'compose', '--env-file', '/dev/null', '-f', str(ROOT / 'compose.yaml')]
    for f in files:
        args += ['-f', str(ROOT / f)]
    p = subprocess.run(args + ['config', '--format', 'json'], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)

class StorageTests(unittest.TestCase):
    def test_template_shares_cache_but_keeps_home_private(self):
        for project in ('storage-a', 'storage-b'):
            cfg = resolve_template(project)
            self.assertEqual(cfg['volumes']['agent-home']['name'], project + '-home')
            self.assertEqual(cfg['volumes']['download-cache']['name'], 'agent-runtime-download-cache')
            runtime = cfg['services']['runtime']
            mounts = {m['target']: m for m in runtime['volumes']}
            self.assertEqual(set(mounts), {'/home/agent', '/shared/caches'})
            self.assertEqual(mounts['/shared/caches']['type'], 'volume')
            self.assertEqual(runtime['environment']['UV_CACHE_DIR'], '/shared/caches/uv')
            self.assertNotIn('CODEX_SHARED_DIR', runtime['environment'])

    def test_template_can_add_codex_files_without_losing_cache(self):
        cfg = resolve_template('storage-a', SHARED_CODEX_DIR='/fixture/codex',
            COMPOSE_FILE='compose.yaml:compose.shared-cache.yaml:compose.shared-codex-files.yaml')
        mounts = {m['target']: m for m in cfg['services']['runtime']['volumes']}
        self.assertEqual(mounts['/shared/caches']['type'], 'volume')
        for name in ('config.toml', 'auth.json'):
            mount = mounts['/shared/codex/' + name]
            self.assertEqual(mount['source'], '/fixture/codex/' + name)
            self.assertTrue(mount['read_only'])
            # Compose versions may omit false/default fields from config JSON.
            self.assertFalse(mount.get('bind', {}).get('create_host_path', False))

    def test_template_can_select_private_caches(self):
        cfg = resolve_template('storage-a', COMPOSE_FILE='compose.yaml')
        runtime = cfg['services']['runtime']
        self.assertEqual([m['target'] for m in runtime['volumes']], ['/home/agent'])
        self.assertNotIn('UV_CACHE_DIR', runtime['environment'])

    def test_private_home_names_are_per_instance_and_overridable(self):
        a, b = resolve(), resolve('storage-b')
        self.assertEqual(a['volumes']['agent-home']['name'], 'storage-a-home')
        self.assertEqual(b['volumes']['agent-home']['name'], 'storage-b-home')
        self.assertEqual(resolve(HOME_VOLUME='existing-home')['volumes']['agent-home']['name'], 'existing-home')

    def test_optional_cache_is_named_shared_and_nocopy(self):
        files = ('compose.shared-cache.yaml',)
        a, b = resolve(files=files), resolve('storage-b', files)
        for cfg in (a, b):
            self.assertEqual(cfg['volumes']['download-cache']['name'], 'agent-runtime-download-cache')
            mounts = {m['target']: m for m in cfg['services']['runtime']['volumes']}
            self.assertEqual(mounts['/shared/caches']['type'], 'volume')
            self.assertTrue(mounts['/shared/caches']['volume']['nocopy'])
            self.assertNotIn('CODEX_SHARED_DIR', cfg['services']['runtime']['environment'])
        custom = resolve(files=files, SHARED_CACHE_VOLUME='trusted-cache')
        self.assertEqual(custom['volumes']['download-cache']['name'], 'trusted-cache')

    def test_explicit_bind_overrides_preserve_legacy_sources(self):
        cfg = resolve(files=('compose.shared-cache.yaml', 'compose.bind-home.yaml', 'compose.shared-cache.bind.yaml'), HOME_DIR='/fixture/home', SHARED_CACHE_DIR='/fixture/cache')
        mounts = {m['target']: m for m in cfg['services']['runtime']['volumes']}
        for target, source in [('/home/agent', '/fixture/home'), ('/shared/caches', '/fixture/cache')]:
            self.assertEqual(mounts[target]['type'], 'bind')
            self.assertEqual(mounts[target]['source'], source)
            self.assertTrue(mounts[target].get('bind', {}).get('create_host_path', True))
            self.assertNotIn('volume', mounts[target])
