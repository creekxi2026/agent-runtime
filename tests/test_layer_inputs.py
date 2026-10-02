"""Offline contracts for independently cached CLI inputs and outputs."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LayerInputTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / 'runtime-deps.json').read_text())

    def split(self, manifest):
        return load('split_dependencies').split_manifest(manifest)

    def test_codex_change_only_changes_codex_input(self):
        before = self.split(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed['versions']['codex'] = '99.0.0'
        changed['npm']['@openai/codex'].update(version='99.0.0', url='https://registry.npmjs.org/codex-new.tgz', integrity='new')
        after = self.split(changed)
        self.assertEqual([k for k in before if before[k] != after[k]], ['codex'])

    def test_multica_change_only_changes_multica_input(self):
        before = self.split(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed['versions']['multica'] = '99.0.0'
        for asset in changed['artifacts']['multica'].values():
            asset.update(url='https://github.com/multica-new.tgz', sha256='a' * 64)
        after = self.split(changed)
        self.assertEqual([k for k in before if before[k] != after[k]], ['multica'])

    def test_core_change_does_not_change_cli_inputs(self):
        before = self.split(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed['versions']['node'] = '99.0.0'
        changed['base_image'] = 'different-base'
        after = self.split(changed)
        self.assertEqual([k for k in before if before[k] != after[k]], ['core'])

    def test_cli_shards_are_minimal_and_split_does_not_mutate_source(self):
        original = copy.deepcopy(self.manifest)
        shards = self.split(self.manifest)
        self.assertEqual(self.manifest, original)
        self.assertEqual(shards['codex']['npm'], {'@openai/codex': self.manifest['npm']['@openai/codex']})
        self.assertEqual(shards['codex']['versions'], {'codex': self.manifest['versions']['codex']})
        self.assertEqual(shards['codex']['artifacts'], {})
        self.assertEqual(shards['multica']['npm'], {})
        self.assertEqual(shards['multica']['artifacts'], {'multica': self.manifest['artifacts']['multica']})
        self.assertNotIn('codex', shards['core']['versions'])
        self.assertNotIn('multica', shards['core']['versions'])
        self.assertNotIn('@openai/codex', shards['core']['npm'])
        self.assertNotIn('multica', shards['core']['artifacts'])

    def test_cli_serialization_is_deterministic_and_cli_writes_three_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / 'manifest.json'
            output = root / 'inputs'
            command = [sys.executable, str(ROOT / 'scripts/split_dependencies.py'), '--manifest', str(manifest), '--output', str(output)]
            manifest.write_text(json.dumps(self.manifest))
            subprocess.run(command, check=True)
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            manifest.write_text(json.dumps(self.manifest, sort_keys=True, indent=4))
            subprocess.run(command, check=True)
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            self.assertEqual(set(before), {'core.json', 'codex.json', 'multica.json'})

    def test_component_schema_rejects_cross_component_inputs(self):
        installer = load('install_tools')
        for component, shard in self.split(self.manifest).items():
            installer.validate_component(shard, component)
            bad = copy.deepcopy(shard)
            bad['versions']['foreign_tool'] = '1.0.0'
            with self.assertRaises(ValueError):
                installer.validate_component(bad, component)
        with self.assertRaises(ValueError):
            installer.validate_component(self.split(self.manifest)['codex'], 'core')

    def test_verify_cli_does_not_probe_absent_core_or_sibling_cli(self):
        installer = load('install_tools')
        shards = self.split(self.manifest)
        with patch.object(installer, 'check_version') as check, patch.object(installer.subprocess, 'run') as run, patch.object(Path, 'read_text', return_value=json.dumps({'version': self.manifest['versions']['codex']})):
            installer.verify(shards['codex'], component='codex')
            check.assert_called_once_with(['/opt/codex/bin/codex', '--version'], self.manifest['versions']['codex'])
            run.assert_not_called()
        with patch.object(installer, 'check_version') as check, patch.object(installer.subprocess, 'run') as run:
            installer.verify(shards['multica'], component='multica')
            check.assert_called_once_with(['/opt/multica/bin/multica', '--version'], self.manifest['versions']['multica'])
            run.assert_not_called()

    def test_codex_install_reuses_core_without_reinstalling_archives(self):
        installer = load('install_tools')
        shard = self.split(self.manifest)['codex']
        with patch.object(installer.os, 'geteuid', return_value=0), patch.object(installer, 'install_npm') as npm, patch.object(installer, 'install_archive') as archive, patch.object(installer, 'verify') as verify:
            installer.install(shard, 'amd64', component='codex')
            archive.assert_not_called()
            self.assertEqual(npm.call_count, 1)
            self.assertEqual(npm.call_args.args[0], shard)
            self.assertEqual(npm.call_args.args[2], Path('/opt/codex'))
            verify.assert_called_once_with(shard, 'codex')

    def test_multica_install_only_exports_its_binary(self):
        installer = load('install_tools')
        shard = self.split(self.manifest)['multica']
        for arch in ('amd64', 'arm64'):
            with patch.object(installer.os, 'geteuid', return_value=0), patch.object(installer, 'install_npm') as npm, patch.object(installer, 'install_archive') as archive, patch.object(installer, 'verify') as verify:
                installer.install(shard, arch, component='multica')
                npm.assert_not_called()
                self.assertEqual(archive.call_count, 1)
                self.assertEqual(archive.call_args.args[:2], ('multica', shard['artifacts']['multica'][arch]))
                self.assertEqual(archive.call_args.args[3], Path('/opt/multica/bin'))
                verify.assert_called_once_with(shard, 'multica')

    def test_archive_export_is_a_regular_binary_in_controlled_directory(self):
        installer = load('install_tools')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'fixture.tar.gz'
            with tarfile.open(archive, 'w:gz') as output:
                for name in ('release/multica', 'release/unrelated-file'):
                    member = tarfile.TarInfo(name)
                    member.size = 7
                    output.addfile(member, io.BytesIO(b'fixture'))
            with patch.object(installer, 'download', return_value=archive):
                installer.install_archive('multica', {}, root, root / 'export/bin')
            files = list((root / 'export/bin').iterdir())
            self.assertEqual([p.name for p in files], ['multica'])
            self.assertFalse(files[0].is_symlink())
            self.assertEqual(files[0].read_bytes(), b'fixture')
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o755)

    def test_codex_npm_prefix_contains_only_codex_and_relative_bin_links(self):
        installer = load('install_tools')
        shard = self.split(self.manifest)['codex']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prefix = root / 'codex'
            binary = prefix / 'node_modules/@openai/codex/bin/codex.js'
            binary.parent.mkdir(parents=True)
            binary.write_text('fixture')
            links = prefix / 'node_modules/.bin'
            links.mkdir()
            (links / 'codex').symlink_to('../@openai/codex/bin/codex.js')
            with patch.object(installer, 'download', return_value=root / 'codex.tgz') as download, patch.object(installer.subprocess, 'run') as run:
                installer.install_npm(shard, root, prefix)
                self.assertEqual(download.call_count, 1)
                self.assertEqual(download.call_args.args[0], shard['npm']['@openai/codex'])
                self.assertEqual(run.call_args.args[0][1:4], ['install', '--prefix', str(prefix)])
            plan = json.loads((prefix / 'package.json').read_text())
            self.assertEqual(plan['dependencies'], {'@openai/codex': shard['versions']['codex']})
            self.assertNotIn('*', plan['allowScripts'])
            self.assertNotIn('pnpm@' + self.manifest['versions']['pnpm'], plan['allowScripts'])
            self.assertEqual((prefix / 'bin/codex').resolve(), binary.resolve())
            self.assertFalse((prefix / 'bin/codex').readlink().is_absolute())

    def test_core_verification_preserves_js_client_and_skips_both_clis(self):
        installer = load('install_tools')
        shard = self.split(self.manifest)['core']
        versions = iter(item['version'] for item in shard['npm'].values())
        with patch.object(installer, 'check_version') as check, patch.object(installer.subprocess, 'run') as run, patch.object(Path, 'read_text', side_effect=lambda: json.dumps({'version': next(versions)})):
            installer.verify(shard, component='core')
            commands = [call.args[0][0] for call in check.call_args_list]
            self.assertFalse(any('codex' in command or 'multica' in command for command in commands))
            self.assertEqual(run.call_count, 2)
            self.assertIn('chromium.connect', run.call_args.args[0][-1])

    def test_dockerfile_has_independent_linked_outputs_and_late_metadata(self):
        text = (ROOT / 'Dockerfile').read_text()
        self.assertIn('FROM ${BASE_IMAGE} AS core', text)
        self.assertIn('FROM core AS codex-tool', text)
        self.assertIn('FROM core AS multica-tool', text)
        self.assertIn('FROM core AS runtime', text)
        self.assertIn('COPY --link --from=codex-tool /opt/codex /opt/codex', text)
        self.assertIn('COPY --link --from=multica-tool /opt/multica/bin/multica /usr/local/bin/multica', text)
        core = text.split('FROM core AS codex-tool')[0]
        self.assertNotIn('COPY runtime-deps.json', core)
        self.assertNotIn('.build-inputs/codex.json', core)
        self.assertNotIn('.build-inputs/multica.json', core)
        self.assertIn('ln -s /opt/codex/bin/codex /opt/agent-tools/bin/codex', core)
        self.assertGreater(text.index('COPY runtime-deps.json'), text.index('COPY --link --from=multica-tool'))
        self.assertNotIn('COPY --from=core', text)


if __name__ == '__main__':
    unittest.main()
