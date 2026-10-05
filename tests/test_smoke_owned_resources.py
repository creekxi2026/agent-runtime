"""No real Docker calls: cleanup must never cross the recorded ownership boundary."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('owned', Path(__file__).with_name('smoke_owned_resources.py'))
assert spec is not None and spec.loader is not None
owned = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owned)

class OwnershipTests(unittest.TestCase):
    def test_failed_create_collision_never_removes_foreign_container(self):
        spec.loader.exec_module(owned)
        calls = []
        def docker(args):
            calls.append(args)
            if args[0] == 'create': raise RuntimeError('name collision')
            return ''
        resources = owned.Resources(docker)
        with self.assertRaises(RuntimeError): resources.create_container(['image'])
        resources.cleanup()
        self.assertFalse(any(a[0] == 'rm' for a in calls))

    def test_start_failure_keeps_created_id_for_cleanup(self):
        calls = []
        cid = 'a' * 64
        resources = None
        def docker(args):
            calls.append(args)
            if args[0] == 'create': return cid
            if args[0] == 'start': raise RuntimeError('start failed')
            if args[0] == 'inspect':
                import json
                return json.dumps([{'Id': cid, 'Config': {'Labels': {owned.LABEL: resources.owner}}}])
            return ''
        resources = owned.Resources(docker)
        with self.assertRaises(RuntimeError): resources.start_container(['image'])
        resources.cleanup()
        self.assertIn(['rm', '-f', cid], calls)

    def test_ownership_mismatch_is_not_removed_and_cleanup_failure_retains_id(self):
        import json
        calls = []
        cid = 'b' * 64
        def docker(args):
            calls.append(args)
            if args[0] == 'create': return cid
            return json.dumps([{'Id': cid, 'Config': {'Labels': {owned.LABEL: 'foreign'}}}])
        resources = owned.Resources(docker)
        resources.create_container(['image'])
        with self.assertRaisesRegex(RuntimeError, 'ownership mismatch'): resources.cleanup()
        self.assertEqual(resources.containers, [cid])
        self.assertFalse(any(a[0] == 'rm' for a in calls))

    def test_concurrent_tasks_never_share_identities(self):
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            owners = list(pool.map(lambda _: owned.Resources(lambda args: '').owner, range(100)))
        self.assertEqual(len(set(owners)), 100)

    def test_volume_collision_is_not_adopted(self):
        calls = []
        def docker(args):
            calls.append(args)
            return '[{"Name":"collision"}]'
        resources = owned.Resources(docker)
        with self.assertRaisesRegex(RuntimeError, 'already exists'): resources.create_volume('home')
        resources.cleanup()
        self.assertFalse(any(a[:2] == ['volume', 'rm'] for a in calls))

    def test_compose_probe_uses_exact_mounts_entrypoint_and_security_without_network_creation(self):
        config = {'image': 'sha256:abc', 'user': '0:0', 'working_dir': '/home/agent',
                  'environment': {'UV_LINK_MODE': 'copy'}, 'volumes': [
                      {'type': 'volume', 'source': 'owned-home', 'target': '/home/agent', 'volume': {'nocopy': True}}],
                  'entrypoint': ['/bin/sh', '-ec', 'exec bootstrap "$@"', 'bootstrap'],
                  'cap_drop': ['ALL'], 'cap_add': ['CHOWN'], 'security_opt': ['no-new-privileges:true'], 'tmpfs': ['/tmp']}
        args = owned.container_args(config, ['sleep', '600'])
        self.assertIn('type=volume,src=owned-home,dst=/home/agent,volume-nocopy', args)
        self.assertEqual(args[args.index('--network') + 1], 'bridge')
        self.assertEqual(args[-5:], ['-ec', 'exec bootstrap "$@"', 'bootstrap', 'sleep', '600'])
        self.assertIn('--pull=never', args)

    def test_cache_fixture_is_loopback_only_in_owned_network_namespace(self):
        args = owned.cache_fixture_args('image', '/owned/fixtures')
        self.assertEqual(args[args.index('--network') + 1], 'none')
        self.assertIn('type=bind,src=/owned/fixtures,dst=/fixtures,readonly', args)
        self.assertIn('--user', args)
        self.assertNotIn('--publish', args)
        self.assertIn('--bind 127.0.0.1', args[-1])
        self.assertIn('exec sleep', args[-1])

    def test_resolved_network_mode_is_preserved(self):
        for mode in ('none', 'host', 'container:task-fixture'):
            with self.subTest(mode=mode):
                args = owned.container_args({'image': 'image', 'network_mode': mode}, ['true'])
                self.assertEqual(args[args.index('--network') + 1], mode)

    def test_read_only_go_directories_are_removed_without_following_symlinks(self):
        import tempfile
        import json
        import subprocess
        cid = 'd' * 64
        create = []
        def docker(args):
            if args[0] == 'create': create[:] = args; return cid
            if args[0] == 'start':
                subprocess.run(['python3', '-c', create[-1].replace('/fixture', str(root))], check=True)
            if args[0] == 'wait': return '0'
            if args[0] == 'inspect':
                return json.dumps([{'Id': cid, 'Config': {'Labels': {owned.LABEL: resources.owner}}}])
            return ''
        with tempfile.TemporaryDirectory() as tmp:
            resources = owned.Resources(docker)
            root = resources.create_fixture(parent=tmp)
            outside = Path(tmp) / 'foreign'; outside.mkdir(mode=0o700)
            (outside / 'keep').write_text('foreign')
            (root / 'link').symlink_to(outside, target_is_directory=True)
            (root / 'dangling').symlink_to('/missing/foreign')
            module = root / 'module'; module.mkdir()
            (module / 'go.mod').write_text('module fixture')
            module.chmod(0o555)
            resources.remove_fixture_tree(root, 'image')
            self.assertFalse(root.exists())
            self.assertEqual((outside / 'keep').read_text(), 'foreign')
            self.assertEqual(outside.stat().st_mode & 0o777, 0o700)

    def test_unrecorded_replaced_and_symlink_roots_never_mount(self):
        import tempfile
        calls = []
        resources = owned.Resources(lambda args: calls.append(args))
        with tempfile.TemporaryDirectory() as tmp:
            foreign = Path(tmp) / 'foreign'; foreign.mkdir()
            (foreign / 'keep').write_text('foreign')
            with self.assertRaisesRegex(RuntimeError, 'Unrecorded'):
                resources.remove_fixture_tree(foreign, 'image')
            root = resources.create_fixture(parent=tmp)
            original = root.with_name(root.name + '-original')
            root.rename(original)
            root.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'replaced'):
                resources.remove_fixture_tree(root, 'image')
            root.rmdir(); root.symlink_to(foreign, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'replaced'):
                resources.remove_fixture_tree(root, 'image')
            self.assertEqual((foreign / 'keep').read_text(), 'foreign')
            self.assertEqual(calls, [])

    def test_uid_mismatch_cleanup_uses_recorded_root_only(self):
        import json
        import os
        import tempfile
        from unittest.mock import patch
        calls = []
        cid = 'c' * 64
        resources = None
        def docker(args):
            calls.append(args)
            if args[0] == 'create': return cid
            if args[0] == 'wait': return '0\n'
            if args[0] == 'inspect':
                return json.dumps([{'Id': cid, 'Config': {'Labels': {owned.LABEL: resources.owner}}}])
            if args[0] == 'start':
                # Emulate the helper's actual script, not a host chmod repair.
                import subprocess
                create = next(a for a in calls if a[0] == 'create')
                script = create[-1].replace('/fixture', str(root))
                subprocess.run(['python3', '-c', script], check=True)
            return ''
        with tempfile.TemporaryDirectory() as tmp:
            resources = owned.Resources(docker)
            root = resources.create_fixture(parent=tmp)
            (root / 'cache').mkdir(); (root / 'cache/file').write_text('cache')
            with patch.object(owned.os, 'chmod', side_effect=PermissionError('different host UID')):
                resources.remove_fixture_tree(root, 'existing-image')
            self.assertFalse(root.exists())
            create = next(a for a in calls if a[0] == 'create')
            self.assertEqual([a for a in create if a.startswith('type=bind,')],
                             ['type=bind,src=' + str(root) + ',dst=/fixture'])
            self.assertEqual(create[create.index('--user') + 1], '0:0')
            self.assertIn('--pull=never', create)
            self.assertEqual(resources.containers, [])
            self.assertIn(['rm', '-f', cid], calls)

    def test_compose_dollar_escape_is_unescaped_for_docker_create(self):
        args = owned.container_args({'image': 'image', 'entrypoint': ['sh', '-ec', 'exec bootstrap "$$@"']}, ['true'])
        self.assertIn('exec bootstrap "$@"', args)

    def test_null_compose_entrypoint_preserves_image_entrypoint(self):
        args = owned.container_args({'image': 'image', 'entrypoint': None}, ['true'])
        self.assertNotIn('--entrypoint', args)
        self.assertEqual(args[-2:], ['image', 'true'])

if __name__ == '__main__': unittest.main()
