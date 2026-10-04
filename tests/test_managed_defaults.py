import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class ManagedDefaultsTests(unittest.TestCase):
    def test_fresh_home_links_skills_and_native_global_browser_config(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            managed = root / 'managed'
            (managed / 'playwright-cli').mkdir(parents=True)
            (managed / 'playwright-cli/SKILL.md').write_text('official fixture')
            config = root / 'browser.json'
            config.write_text('{"browser":{}}')
            home = root / 'home'
            env = {**os.environ, 'HOME': str(home), 'CODEX_HOME': str(home / '.codex'),
                   'AGENT_MANAGED_SKILLS_DIR': str(managed), 'AGENT_PLAYWRIGHT_CONFIG': str(config)}
            result = subprocess.run(['sh', 'entrypoint.sh', 'true'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home / '.agents/skills/playwright-cli').is_symlink())
            self.assertEqual((home / '.playwright/cli.config.json').resolve(), config.resolve())

    def test_user_customizations_survive_and_managed_links_follow_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            managed = root / 'managed'
            skill = managed / 'playwright-cli'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('v1')
            home = root / 'home'
            custom = home / '.agents/skills/lark-doc'
            custom.mkdir(parents=True)
            (custom / 'SKILL.md').write_text('custom')
            (home / '.playwright').mkdir()
            browser = home / '.playwright/cli.config.json'
            browser.write_text('custom config')
            env = {**os.environ, 'HOME': str(home), 'CODEX_HOME': str(home / '.codex'),
                   'AGENT_MANAGED_SKILLS_DIR': str(managed)}
            for revision in ['v1', 'v2']:
                (skill / 'SKILL.md').write_text(revision)
                subprocess.run(['sh', 'entrypoint.sh', 'true'], env=env, check=True)
                self.assertEqual((home / '.agents/skills/playwright-cli/SKILL.md').read_text(), revision)
                self.assertEqual((custom / 'SKILL.md').read_text(), 'custom')
                self.assertEqual(browser.read_text(), 'custom config')

if __name__ == '__main__':
    unittest.main()
