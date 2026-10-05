"""Go module settings resolved by Compose without starting containers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "GOPROXY": "https://proxy.golang.org,direct",
    "GOSUMDB": "sum.golang.org",
    "GOPRIVATE": "",
}


class GoProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker"):
            raise unittest.SkipTest("Docker Compose CLI is required (no daemon needed)")
        result = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if result.returncode:
            raise unittest.SkipTest("Docker Compose CLI is unavailable")

    def resolve(self, values=None, *, sample=False, shell=None):
        with tempfile.TemporaryDirectory(prefix="runtime-go-proxy-test-") as directory:
            env_file = Path(directory) / "runtime.env"
            contents = (ROOT / "runtime.env.example").read_text() if sample else (
                "COMPOSE_PROJECT_NAME=go-proxy-test\n"
            )
            contents += "\n" + "".join(f"{key}={value}\n" for key, value in (values or {}).items())
            env_file.write_text(contents)
            env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
            env.update(shell or {})
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(env_file), "-f",
                 str(ROOT / "compose.yaml"), "config", "--format", "json"],
                env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            resolved = json.loads(result.stdout)["services"]["runtime"]["environment"]
            return {key: resolved.get(key) for key in DEFAULTS}

    def test_unset_settings_use_global_defaults(self):
        self.assertEqual(self.resolve(), DEFAULTS)

    def test_blank_settings_use_global_defaults(self):
        self.assertEqual(self.resolve(dict.fromkeys(DEFAULTS, "")), DEFAULTS)

    def test_env_file_overrides_are_preserved(self):
        values = {
            "GOPROXY": "https://goproxy.cn,direct",
            "GOSUMDB": "sum.golang.org",
            "GOPRIVATE": "*.example.com,git.example.org/team/*",
        }
        self.assertEqual(self.resolve(values), values)
        values.update(GOPROXY="direct", GOSUMDB="sum.example.com")
        self.assertEqual(self.resolve(values), values)

    def test_shell_overrides_take_precedence(self):
        values = {
            "GOPROXY": "https://goproxy.cn,direct",
            "GOSUMDB": "sum.example.com",
            "GOPRIVATE": "git.example.org/*",
        }
        self.assertEqual(self.resolve(DEFAULTS, shell=values), values)

    def test_template_exposes_global_defaults(self):
        assignments = dict(
            line.split("=", 1) for line in (ROOT / "runtime.env.example").read_text().splitlines()
            if line and not line.startswith("#") and "=" in line
        )
        self.assertEqual({key: assignments.get(key) for key in DEFAULTS}, DEFAULTS)
        self.assertEqual(self.resolve(sample=True), DEFAULTS)


if __name__ == "__main__":
    unittest.main()
