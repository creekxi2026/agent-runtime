import json
import os
import subprocess
import unittest


class DeploymentTests(unittest.TestCase):
    def resolve(self, extra=None, files=()):
        env = {key: os.environ[key] for key in ('PATH', 'HOME') if key in os.environ}
        env.update(COMPOSE_PROJECT_NAME='agent-mount-test', HOME_DIR='/fixture/home')
        env.update(extra or {})
        command = ['docker', 'compose', '--env-file', '/dev/null', '-f', 'compose.yaml']
        for file in files:
            command += ['-f', file]
        result = subprocess.run(command + ['config', '--format', 'json'],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['services']['runtime']

    def test_default_deployment_is_private_and_image_is_selectable(self):
        runtime = self.resolve()
        self.assertEqual(runtime['image'], 'ghcr.io/creekxi2026/agent-runtime:latest')
        self.assertEqual({m['target'] for m in runtime['volumes']}, {'/home/agent'})
        self.assertEqual(runtime['volumes'][0]['type'], 'volume')
        self.assertEqual(runtime['volumes'][0]['source'], 'agent-home')
        self.assertTrue(runtime['volumes'][0]['volume']['nocopy'])
        self.assertNotIn('CODEX_SHARED_DIR', runtime['environment'])
        self.assertEqual(self.resolve({'RUNTIME_IMAGE': 'example/runtime:test'})['image'], 'example/runtime:test')

    def test_shared_codex_is_explicit_readonly_directory_override(self):
        runtime = self.resolve({'SHARED_CODEX_DIR': '/fixture/codex'}, ('compose.shared-codex.yaml',))
        mounts = {m['target']: m for m in runtime['volumes']}
        self.assertEqual(set(mounts), {'/home/agent', '/shared/codex'})
        self.assertTrue(mounts['/shared/codex']['read_only'])
        self.assertFalse(mounts['/shared/codex'].get('bind', {}).get('create_host_path', False))
        self.assertEqual(runtime['environment']['CODEX_SHARED_DIR'], '/shared/codex')

    def test_smoke_covers_private_default_then_optional_shared_mode(self):
        from pathlib import Path
        smoke = (Path(__file__).resolve().parents[1] / 'tests/smoke.sh').read_text()
        self.assertNotIn('DATA_DIR', smoke)
        self.assertNotIn('tests/compose.readonly.yaml', smoke)
        self.assertIn('private-auth-fixture', smoke)
        # Exercise the runner's real Compose resolution and generated create args,
        # stopping before any Docker resource is created.
        import sys
        import tempfile
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import smoke_compose_owned as runner
        with tempfile.TemporaryDirectory() as parent:
            resources = runner.Resources(lambda args: '')
            fixture = str(resources.create_fixture(parent=parent))
            (Path(fixture) / 'owned-resources.json').write_text(json.dumps({
                'owner': resources.owner, 'containers': [], 'fixtures': resources.fixtures}))
            shared = str(Path(fixture) / 'codex')
            for enabled in ('false', 'true'):
                with self.subTest(shared=enabled), patch.dict(os.environ, {'SHARED_CODEX_DIR': shared, 'IMAGE': 'example/runtime:test'}), \
                     patch.object(sys, 'argv', ['runner', 'run', fixture, 'skills-probe', enabled, 'run', 'runtime', 'true']), \
                     patch.object(runner.Resources, 'create_container', side_effect=RuntimeError('stop before create')) as create:
                    with self.assertRaisesRegex(RuntimeError, 'stop before create'):
                        runner.main()
                    args = create.call_args.args[0]
                    self.assertEqual(args[args.index('--network') + 1], 'none')
                    mounts = [args[i + 1] for i, arg in enumerate(args) if arg == '--mount']
                    self.assertIn('type=bind,src=' + fixture + '/skills-probe-home,dst=/home/agent', mounts)
                    codex = 'type=bind,src=' + shared + ',dst=/shared/codex,readonly'
                    if enabled == 'true':
                        self.assertIn(codex, mounts)
                        self.assertIn('CODEX_SHARED_DIR=/shared/codex', args)
                    else:
                        self.assertNotIn(codex, mounts)
                        self.assertNotIn('CODEX_SHARED_DIR=/shared/codex', args)

    def test_current_product_docs_match_private_home_and_image_selection(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        for name in ('README.md', 'runtime.env.example', 'compose.yaml'):
            text = (root / name).read_text()
            self.assertIn('HOME_VOLUME', text)
            self.assertIn('RUNTIME_IMAGE', text)
            for retired in ('DATA_DIR', 'IMAGE_TAG', 'MAX_CONCURRENT_TASKS` 不再使用'):
                self.assertNotIn(retired, text)
        self.assertIn('compose.shared-codex.yaml', (root / 'README.md').read_text())

    def test_deployment_uses_only_container_native_browser(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        self.assertNotIn('PLAYWRIGHT_WS_ENDPOINT', self.resolve()['environment'])
        for name in ('runtime.env.example', 'README.md', '.github/workflows/verify.yml', '.github/workflows/publish.yml'):
            text = (root / name).read_text()
            self.assertNotIn('PLAYWRIGHT_WS_ENDPOINT', text, name)
            self.assertNotIn('remote-browser', text, name)
            self.assertNotIn('TEST_REMOTE_BROWSER', text, name)
        self.assertFalse(list((root / 'tests').glob('remote-browser*')))


if __name__ == '__main__':
    unittest.main()
