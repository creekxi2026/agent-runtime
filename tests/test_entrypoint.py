"""Local regression test; fake CLIs isolate auth/network, not runtime E2E."""
import os
import pathlib
import subprocess
import tempfile
import unittest

ENTRYPOINT = os.environ.get("ENTRYPOINT_UNDER_TEST", "entrypoint.sh")


class BootstrapTests(unittest.TestCase):
    def test_preserves_existing_lark_users_and_initializes_only_when_absent(self):
        with tempfile.TemporaryDirectory(prefix="agent-entrypoint-test-") as directory:
            root = pathlib.Path(directory)
            tools = root / "bin"
            tools.mkdir()
            for name, code in (("multica", 0), ("lark-cli", 43)):
                path = tools / name
                path.write_text(f"#!/bin/sh\nexit {code}\n")
                path.chmod(0o700)
            config_dir = root / ".lark-cli"
            config_dir.mkdir()
            config = config_dir / "config.json"
            fixture = b'{"apps":[{"users":[{"userOpenId":"fixture-user"}]}]}'
            config.write_bytes(fixture)
            env = {
                "PATH": str(tools) + os.pathsep + os.defpath,
                "HOME": str(root),
                "CODEX_HOME": str(root / ".codex"),
                "LARKSUITE_CLI_CONFIG_DIR": str(config_dir),
                "LARK_APP_ID": "fixture-app",
                "LARK_APP_SECRET": "fixture-not-a-real-secret",
            }
            command = ["sh", ENTRYPOINT, "multica", "daemon", "start"]
            for _ in range(2):
                result = subprocess.run(command, env=env, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, "Existing config must skip lark-cli init")
                self.assertEqual(config.read_bytes(), fixture)
            config.unlink()
            result = subprocess.run(command, env=env, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 43, "Absent config must invoke lark-cli init")


if __name__ == "__main__":
    unittest.main()
