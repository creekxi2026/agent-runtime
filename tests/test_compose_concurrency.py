"""Exercise optional daemon concurrency with the real Compose config resolver."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
KEY = "MULTICA_DAEMON_MAX_CONCURRENT_TASKS"


class ComposeConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker"):
            raise unittest.SkipTest("Docker Compose CLI is required (no daemon needed)")
        result = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if result.returncode:
            raise unittest.SkipTest("Docker Compose CLI is unavailable")

    def resolve(self, extra="", *, sample=False, shell_value=None):
        with tempfile.TemporaryDirectory(prefix="runtime-compose-test-") as directory:
            env_file = Path(directory) / "runtime.env"
            contents = (ROOT / "runtime.env.example").read_text() if sample else (
                "COMPOSE_PROJECT_NAME=concurrency-test\n"
                "SHARED_CODEX_DIR=./shared-codex\n"
                "SHARED_SKILLS_DIR=./shared-skills\n"
            )
            env_file.write_text(contents + "\n" + extra)
            env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
            if shell_value is not None:
                env[KEY] = shell_value
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(env_file), "-f",
                 str(ROOT / "compose.yaml"), "config", "--format", "json"],
                env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)["services"]["runtime"]["environment"]

    def test_unset_has_no_concurrency_override(self):
        # A null Compose value is unresolved and removed from the container env.
        self.assertIsNone(self.resolve().get(KEY))

    def test_sample_has_no_concurrency_override(self):
        self.assertIsNone(self.resolve(sample=True).get(KEY))

    def test_explicit_env_file_limit_is_preserved(self):
        for value in ("1", "4", "50"):
            with self.subTest(value=value):
                self.assertEqual(self.resolve(f"{KEY}={value}\n")[KEY], value)

    def test_shell_override_is_preserved(self):
        self.assertEqual(self.resolve(shell_value="50")[KEY], "50")


if __name__ == "__main__":
    unittest.main()
