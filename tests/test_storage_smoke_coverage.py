"""Coverage wiring guards; actual storage assertions run in both real smoke modes."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class StorageSmokeTests(unittest.TestCase):
    def test_named_probe_and_bind_probe_both_are_wired(self):
        shell = (ROOT / 'tests/smoke.sh').read_text()
        self.assertIn('tests/named_volume_smoke.py', shell)
        self.assertIn('tests/shared_cache_smoke.py', shell)

    def test_private_home_markers_survive_recreation_without_cross_tenant_visibility(self):
        smoke = (ROOT / 'tests/shared_cache_smoke.py').read_text()
        self.assertIn('private-home-marker', smoke)
        self.assertIn('test ! -e "$HOME/private-home-marker"', smoke)
        self.assertIn('test "$(cat "$HOME/private-home-marker")" = tenant-a', smoke)

    def test_shell_cleanup_has_no_project_wide_delete_or_unrecorded_helper_container(self):
        shell = (ROOT / 'tests/smoke.sh').read_text()
        self.assertNotIn('down --volumes', shell)
        self.assertNotIn('docker rm -f', shell)
        self.assertIn('tests/smoke_compose_owned.py cleanup', shell)

    def test_all_raw_smoke_runs_use_owned_create_and_inspected_image(self):
        shell = (ROOT / 'tests/smoke.sh').read_text()
        self.assertIn('tests/smoke_compose_owned.py docker-run', shell)
        self.assertIn('docker image inspect', shell)
        self.assertNotIn('timeout 30 docker run', shell)

if __name__ == '__main__': unittest.main()
