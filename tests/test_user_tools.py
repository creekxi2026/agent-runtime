"""Private tool lifecycle: first seed, user ownership, recovery and recreation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('init_user_tools', Path(__file__).resolve().parents[1] / 'scripts/init_user_tools.py')
init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init)


class UserToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / 'home'
        self.home.mkdir()
        self.source = root / 'package'
        self.source.mkdir()
        (self.source / 'package.json').write_text(json.dumps({'name': 'example', 'version': '1.0.0', 'bin': {'example': 'cli.js'}}))
        (self.source / 'cli.js').write_text('first version')
        self.tool = init.npm_tool(self.source, 'example')
        self.target = self.home / self.tool['target']

    def test_seed_then_user_update_and_removal_survive_new_image(self):
        init.seed(self.home, [self.tool])
        self.assertEqual((self.home / '.local/bin/example').resolve(), (self.target / 'cli.js').resolve())
        (self.target / 'cli.js').write_text('user updated')
        (self.source / 'cli.js').write_text('new image')
        init.seed(self.home, [self.tool])
        self.assertEqual((self.target / 'cli.js').read_text(), 'user updated')
        (self.home / '.local/bin/example').unlink()
        init.seed(self.home, [self.tool])
        self.assertFalse((self.home / '.local/bin/example').exists())

    def test_existing_package_or_command_is_never_overwritten(self):
        self.target.mkdir(parents=True)
        (self.target / 'user-data').write_text('keep')
        init.seed(self.home, [self.tool])
        self.assertEqual((self.target / 'user-data').read_text(), 'keep')
        self.assertFalse((self.target / 'cli.js').exists())
        other = dict(self.tool, id='npm:other', target=Path('.local/lib/node_modules/other'), bins={'example': 'cli.js'})
        command = self.home / '.local/bin/example'
        command.write_text('custom')
        init.seed(self.home, [other])
        self.assertEqual(command.read_text(), 'custom')
        self.assertFalse((self.home / other['target']).exists())

    def test_partial_copy_and_publish_interruption_recover(self):
        def fail_copy(src, dst, **kwargs):
            Path(dst).mkdir()
            (Path(dst) / 'partial').write_text('partial')
            raise OSError('interrupted copy')
        with patch.object(init.shutil, 'copytree', side_effect=fail_copy):
            with self.assertRaises(OSError):
                init.seed(self.home, [self.tool])
        self.assertFalse(self.target.exists())
        with patch.object(Path, 'symlink_to', side_effect=OSError('interrupted link')):
            with self.assertRaises(OSError):
                init.seed(self.home, [self.tool])
        self.assertTrue(self.target.exists())
        init.seed(self.home, [self.tool])
        self.assertEqual((self.home / '.local/bin/example').read_text(), 'first version')
        self.assertFalse((self.home / '.local/share/agent-runtime/staging').exists())

    def test_rejects_home_escape_and_cross_arch_reuse(self):
        (self.home / '.local').symlink_to(self.source)
        with self.assertRaises(RuntimeError):
            init.seed(self.home, [self.tool])
        (self.home / '.local').unlink()
        init.seed(self.home, [self.tool], architecture='arm64')
        with self.assertRaises(RuntimeError):
            init.seed(self.home, [self.tool], architecture='amd64')


if __name__ == '__main__':
    unittest.main()
