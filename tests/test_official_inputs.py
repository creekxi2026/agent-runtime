import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class OfficialInputsTests(unittest.TestCase):
    def test_debian_metadata_rejects_wrong_arch_and_missing_checksum(self):
        spec = importlib.util.spec_from_file_location('official_inputs', ROOT / 'scripts/official_inputs.py')
        self.assertTrue(spec.origin and Path(spec.origin).exists(), 'Official inputs resolver is missing')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        record = 'Package: chromium\nVersion: 154.0-1\nArchitecture: arm64\nSHA256: ' + 'a'*64 + '\n'
        self.assertEqual(module.package_record(record, 'chromium', 'arm64')['version'], '154.0-1')
        with self.assertRaises(ValueError):
            module.package_record(record, 'chromium', 'amd64')
        with self.assertRaises(ValueError):
            module.package_record(record.replace('SHA256:', 'Other:'), 'chromium', 'arm64')

    def test_apt_partial_repository_failure_is_fatal(self):
        import sys
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT / 'scripts'))
        import official_inputs
        record = lambda n: 'Package: ' + n + '\nVersion: 154.0-1\nArchitecture: all\nSHA256: ' + 'a'*64 + '\n'
        output = '\n---PACKAGE---\n'.join(record(n) for n in ['chromium','chromium-common','chromium-sandbox']) + '\n---PACKAGE---\n'
        with patch.object(official_inputs.subprocess, 'check_output', return_value=output) as command:
            official_inputs.chromium_inputs('debian@sha256:'+'a'*64)
            for call in command.call_args_list:
                self.assertIn('APT::Update::Error-Mode=any', ' '.join(call.args[0]))

    def test_content_changes_trigger_build_without_cli_changes(self):
        import copy
        import hashlib
        import json
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        from check_upstream import requires_build
        from resolve_tools import serialize
        lock = json.loads((ROOT / 'runtime-deps.json').read_text())
        lock['official_skills'] = {'lark': {'commit': 'a'*40, 'tree': 'b'*40}}
        lock['chromium'] = {'arm64': {'chromium': {'version': '154.0-1'}}}
        fingerprint = lambda value: 'sha256:' + hashlib.sha256(serialize(value).encode()).hexdigest()
        base = lock['base_digest']
        labels = {arch: {'org.opencontainers.image.base.digest': base,
                        'org.opencontainers.image.revision': 'c'*40,
                        'io.creek.runtime.dependencies': fingerprint(lock)} for arch in ['amd64','arm64']}
        self.assertFalse(requires_build(base, 'c'*40, labels, fingerprint(lock)))
        for field in ['official_skills', 'chromium']:
            updated = copy.deepcopy(lock)
            updated[field] = {'changed': True}
            self.assertTrue(requires_build(base, 'c'*40, labels, fingerprint(updated)))

if __name__ == '__main__': unittest.main()
