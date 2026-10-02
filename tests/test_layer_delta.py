"""Unit checks for real layer-delta acceptance, not simulated build results."""
import copy
from pathlib import Path
import unittest
from layer_delta_probe import compare


class LayerDeltaAcceptanceTests(unittest.TestCase):
    def layers(self):
        return [{'kind': kind, 'digest': 'sha256:' + str(i) * 64,
                 'size': size, 'created_by': kind}
                for i, (kind, size) in enumerate([('core', 500), ('codex', 80), ('multica', 20), ('metadata', 1)])]

    def test_only_codex_and_metadata_change(self):
        current = self.layers()
        prior = copy.deepcopy(current)
        prior[1]['digest'] = 'sha256:' + 'a' * 64
        prior[3]['digest'] = 'sha256:' + 'b' * 64
        self.assertEqual(compare(current, prior, 'codex')['update_download_bytes'], 81)

    def test_unrelated_tool_or_core_invalidation_fails(self):
        for index in (0, 2):
            current = self.layers()
            prior = copy.deepcopy(current)
            prior[1]['digest'] = 'sha256:' + 'a' * 64
            prior[index]['digest'] = 'sha256:' + 'b' * 64
            with self.assertRaises(AssertionError):
                compare(current, prior, 'codex')

    def test_metadata_only_is_not_a_real_version_comparison(self):
        current = self.layers()
        prior = copy.deepcopy(current)
        prior[3]['digest'] = 'sha256:' + 'b' * 64
        with self.assertRaises(AssertionError):
            compare(current, prior, 'codex')

    def test_persistent_cache_uses_github_not_an_extra_image_tag(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ('publish.yml', 'verify.yml'):
            text = (root / '.github/workflows' / filename).read_text()
            self.assertIn('cache-from: type=gha,version=2,scope=runtime-${{ matrix.arch }}', text)
            self.assertIn('cache-to: type=gha,version=2,scope=runtime-${{ matrix.arch }},mode=max', text)
            self.assertNotIn('type=registry', text)
            self.assertIn('scripts/split_dependencies.py', text)


if __name__ == '__main__':
    unittest.main()
