"""Exercise the CLI-version to setup-compose release-tag handoff in both workflows."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / '.github/workflows'


class ComposeWorkflowVersionTests(unittest.TestCase):
    def record_version(self, workflow, version, exit_code=0):
        command = re.search(
            r'        id: compose-version\n        run: (.+)',
            (WORKFLOWS / workflow).read_text(),
        ).group(1)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            docker = folder / 'docker'
            docker.write_text(
                '#!/bin/sh\n'
                '[ "$*" = "compose version --short" ] || exit 99\n'
                'printf "%s\\n" "$TEST_COMPOSE_VERSION"\n'
                'exit "$TEST_COMPOSE_EXIT"\n'
            )
            docker.chmod(0o755)
            output = folder / 'output'
            env = dict(PATH=directory + os.pathsep + os.environ['PATH'],
                       GITHUB_OUTPUT=str(output), TEST_COMPOSE_VERSION=version,
                       TEST_COMPOSE_EXIT=str(exit_code))
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', command],
                                    env=env, capture_output=True, text=True)
            return result, output.read_text() if output.exists() else ''

    def test_short_versions_become_release_tags_without_duplicate_prefix(self):
        for workflow in ('publish.yml', 'verify.yml'):
            for version in ('5.6.0', 'v5.6.0'):
                with self.subTest(workflow=workflow, version=version):
                    result, output = self.record_version(workflow, version)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output, 'version=v5.6.0\n')

    def test_failed_version_command_does_not_publish_job_output(self):
        for workflow in ('publish.yml', 'verify.yml'):
            with self.subTest(workflow=workflow):
                result, output = self.record_version(workflow, '', exit_code=9)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, '')
