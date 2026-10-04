import json
import os
import subprocess
import unittest

class SkillsMountTests(unittest.TestCase):
    def test_default_compose_needs_no_shared_skills_and_preserves_codex(self):
        env = {**os.environ, 'COMPOSE_PROJECT_NAME': 'agent-mount-test',
               'SHARED_CODEX_DIR': '/fixture/codex', 'DATA_DIR': '/fixture/home'}
        env.pop('SHARED_SKILLS_DIR', None)
        result = subprocess.run(['docker','compose','-f','compose.yaml','config','--format','json'],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        mounts = json.loads(result.stdout)['services']['runtime']['volumes']
        self.assertEqual({m['target'] for m in mounts}, {'/home/agent','/shared/codex'})
        self.assertFalse(next(m for m in mounts if m['target']=='/shared/codex').get('read_only',False))

if __name__ == '__main__': unittest.main()
