import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verify_dependency_lock import verify


class DependencyLockTests(unittest.TestCase):
    def test_accepts_exact_artifact_and_base_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lock.json'
            base = 'debian:stable-slim@sha256:' + 'a' * 64
            data = json.dumps({'base_image': base}).encode()
            path.write_bytes(data)
            digest = 'sha256:' + hashlib.sha256(data).hexdigest()
            self.assertEqual(verify(path, digest, base)['base_image'], base)
            with self.assertRaises(ValueError):
                verify(path, 'sha256:' + 'b' * 64, base)
            with self.assertRaises(ValueError):
                verify(path, digest, 'different-base')

    def test_both_workflows_verify_the_shared_artifact(self):
        root = Path(__file__).resolve().parents[1]
        for name in ['verify.yml', 'publish.yml']:
            text = (root / '.github/workflows' / name).read_text()
            self.assertIn('name: runtime-deps', text)
            self.assertIn('DEPENDENCY_DIGEST:', text)
            self.assertLess(text.index('run: python3 scripts/verify_dependency_lock.py'), text.index('uses: docker/build-push-action@'))
            self.assertIn('tests/remote-browser.sh', text)


if __name__ == '__main__':
    unittest.main()
