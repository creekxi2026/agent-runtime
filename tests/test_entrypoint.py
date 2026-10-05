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


class SharedCodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-shared-codex-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.shared = self.root / "shared-codex"
        self.shared.mkdir()
        self.home = self.root / "user"
        self.codex = self.home / ".codex"
        self.codex.mkdir(parents=True)
        self.config = b'cli_auth_credentials_store = "file"\n'
        self.auth = b'{"auth_mode":"apikey","OPENAI_API_KEY":"INVALID-TEST-ONLY-NOT-A-REAL-KEY"}\n'
        (self.shared / "config.toml").write_bytes(self.config)
        (self.shared / "auth.json").write_bytes(self.auth)
        self.env = {
            "PATH": os.defpath, "HOME": str(self.home),
            "CODEX_HOME": str(self.codex), "CODEX_SHARED_DIR": str(self.shared),
        }

    def run_entrypoint(self, *command):
        return subprocess.run(
            ["sh", getattr(self, "entrypoint", ENTRYPOINT), *(command or ("true",))], env=self.env,
            capture_output=True, text=True, timeout=5,
        )

    def assert_started(self, *command):
        result = self.run_entrypoint(*command)
        self.assertEqual(result.returncode, 0, result.stderr)

    def use_canonical_shared_fixture(self):
        # Remap only the container's fixed mount in a disposable script copy.
        # No test reads or writes the real /shared mount or the user's HOME.
        script = self.root / "entrypoint.sh"
        script.write_text(pathlib.Path(ENTRYPOINT).read_text().replace(
            "/shared/codex", str(self.shared)))
        self.entrypoint = str(script)

    def test_private_shared_private_restores_backups_without_consuming_them(self):
        self.use_canonical_shared_fixture()
        for name in ("config.toml", "auth.json"):
            (self.codex / name).write_text("private-" + name)
        self.assert_started()
        self.env.pop("CODEX_SHARED_DIR")
        # Restore both a dangling config link and a still-mounted auth link.
        (self.shared / "config.toml").unlink()
        self.assert_started()
        self.assert_started()
        for name in ("config.toml", "auth.json"):
            target = self.codex / name
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_text(), "private-" + name)
            self.assertEqual((self.codex / (name + ".before-shared")).read_text(),
                             "private-" + name)

    def test_private_mode_clears_only_known_dead_links_without_backups(self):
        self.use_canonical_shared_fixture()
        self.assert_started()
        self.env.pop("CODEX_SHARED_DIR")
        for name in ("config.toml", "auth.json"):
            (self.shared / name).unlink()
        self.assert_started()
        self.assert_started()
        for name in ("config.toml", "auth.json"):
            target = self.codex / name
            self.assertFalse(target.is_symlink())
            self.assertFalse(target.exists())

    def test_private_mode_live_shared_link_without_backup_fails_before_restore(self):
        self.use_canonical_shared_fixture()
        (self.codex / "config.toml").write_text("private-config")
        self.assert_started()
        self.env.pop("CODEX_SHARED_DIR")
        result = self.run_entrypoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("auth.json", result.stderr)
        self.assertIn("CODEX_SHARED_DIR", result.stderr)
        self.assertIn("before-shared", result.stderr)
        for name in ("config.toml", "auth.json"):
            self.assertEqual(os.readlink(self.codex / name), str(self.shared / name))
        self.assertEqual((self.codex / "config.toml.before-shared").read_text(),
                         "private-config")
        self.assertEqual((self.shared / "auth.json").read_bytes(), self.auth)

    def test_private_mode_invalid_backup_fails_before_changing_either_link(self):
        self.use_canonical_shared_fixture()
        for name in ("config.toml", "auth.json"):
            (self.codex / name).write_text("private-" + name)
        self.assert_started()
        backup = self.codex / "auth.json.before-shared"
        backup.unlink()
        backup.mkdir()
        (backup / "valuable").write_text("keep-this")
        self.env.pop("CODEX_SHARED_DIR")
        result = self.run_entrypoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(backup), result.stderr)
        self.assertIn("manually", result.stderr)
        for name in ("config.toml", "auth.json"):
            self.assertEqual(os.readlink(self.codex / name), str(self.shared / name))
        self.assertEqual((backup / "valuable").read_text(), "keep-this")
        self.assertEqual((self.codex / "config.toml.before-shared").read_text(),
                         "private-config.toml")

    def test_private_mode_preserves_unknown_links_and_their_backups(self):
        self.use_canonical_shared_fixture()
        self.env.pop("CODEX_SHARED_DIR")
        originals = {
            "config.toml": self.root / "missing-custom-config",
            "auth.json": self.shared / "config.toml",  # Wrong basename is not managed.
        }
        for name, original in originals.items():
            (self.codex / name).symlink_to(original)
            (self.codex / (name + ".before-shared")).write_text("older-" + name)
        self.assert_started()
        self.assert_started()
        for name, original in originals.items():
            self.assertEqual(os.readlink(self.codex / name), str(original))
            self.assertEqual((self.codex / (name + ".before-shared")).read_text(),
                             "older-" + name)
        self.assertEqual((self.shared / "config.toml").read_bytes(), self.config)

    def test_private_mode_backup_conflict_never_overwrites_private_files(self):
        self.use_canonical_shared_fixture()
        self.env.pop("CODEX_SHARED_DIR")
        for name in ("config.toml", "auth.json"):
            (self.codex / name).write_text("current-" + name)
            (self.codex / (name + ".before-shared")).write_text("older-" + name)
        self.assert_started()
        for name in ("config.toml", "auth.json"):
            self.assertEqual((self.codex / name).read_text(), "current-" + name)
            self.assertEqual((self.codex / (name + ".before-shared")).read_text(),
                             "older-" + name)

    def test_private_mode_restores_symlink_backups_without_following_them(self):
        self.use_canonical_shared_fixture()
        originals = {
            "config.toml": self.root / "missing-private-config",
            "auth.json": self.root / "private-auth",
        }
        originals["auth.json"].write_text("private-auth-fixture")
        for name, original in originals.items():
            (self.codex / name).symlink_to(original)
        self.assert_started()
        self.env.pop("CODEX_SHARED_DIR")  # Shared files remain mounted and untouched.
        self.assert_started()
        self.assert_started()
        for name, original in originals.items():
            self.assertEqual(os.readlink(self.codex / name), str(original))
            self.assertEqual(os.readlink(self.codex / (name + ".before-shared")),
                             str(original))
        self.assertEqual(originals["auth.json"].read_text(), "private-auth-fixture")
        self.assertEqual((self.shared / "auth.json").read_bytes(), self.auth)

    def test_private_mode_preserves_auth_and_sessions_without_shared_links(self):
        self.env.pop('CODEX_SHARED_DIR')
        (self.codex / 'auth.json').write_text('private-auth-fixture')
        (self.codex / 'config.toml').write_text('private-config-fixture')
        (self.codex / 'sessions').mkdir()
        (self.codex / 'sessions/history').write_text('private-history')
        self.assert_started()
        self.assert_started()
        self.assertFalse((self.codex / 'auth.json').is_symlink())
        self.assertEqual((self.codex / 'auth.json').read_text(), 'private-auth-fixture')
        self.assertEqual((self.codex / 'config.toml').read_text(), 'private-config-fixture')
        self.assertEqual((self.codex / 'sessions/history').read_text(), 'private-history')

    def test_links_only_config_and_auth_and_is_idempotent(self):
        sessions = self.codex / "multica-sessions" / "task"
        sessions.mkdir(parents=True)
        (sessions / "history").write_text("private-session")
        (self.codex / "cache").write_text("private-cache")
        self.assert_started()
        inodes = {}
        for name in ("config.toml", "auth.json"):
            path = self.codex / name
            self.assertTrue(path.is_symlink())
            self.assertEqual(os.readlink(path), str(self.shared / name))
            inodes[name] = path.lstat().st_ino
        self.assert_started()
        for name, inode in inodes.items():
            self.assertEqual((self.codex / name).lstat().st_ino, inode)
            self.assertFalse((self.codex / (name + ".before-shared")).exists())
        self.assertEqual((sessions / "history").read_text(), "private-session")
        self.assertEqual((self.codex / "cache").read_text(), "private-cache")

    def test_preserves_private_files_as_fixed_backups(self):
        for name in ("config.toml", "auth.json"):
            (self.codex / name).write_text("private-" + name)
        self.assert_started()
        self.assert_started()
        for name in ("config.toml", "auth.json"):
            self.assertEqual((self.codex / (name + ".before-shared")).read_text(), "private-" + name)
            self.assertEqual((self.codex / name).read_bytes(), (self.shared / name).read_bytes())

    def test_backup_conflict_fails_before_changing_either_file(self):
        for dangling in (False, True):
            with self.subTest(dangling_backup=dangling):
                for name in ("config.toml", "auth.json"):
                    (self.codex / name).write_text("private-" + name)
                backup = self.codex / "auth.json.before-shared"
                if backup.exists() or backup.is_symlink():
                    backup.unlink()
                if dangling:
                    backup.symlink_to(self.root / "absent")
                else:
                    backup.write_text("older-private-auth")
                result = self.run_entrypoint()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("auth.json.before-shared", result.stderr)
                for name in ("config.toml", "auth.json"):
                    self.assertFalse((self.codex / name).is_symlink())
                    self.assertEqual((self.codex / name).read_text(), "private-" + name)
                if dangling:
                    self.assertTrue(backup.is_symlink())
                else:
                    self.assertEqual(backup.read_text(), "older-private-auth")
                backup.unlink()

    def test_preserves_existing_dangling_symlink(self):
        original = self.root / "missing-private-config"
        (self.codex / "config.toml").symlink_to(original)
        self.assert_started()
        self.assertEqual(os.readlink(self.codex / "config.toml.before-shared"), str(original))

    def test_atomic_replace_is_visible_to_both_private_homes(self):
        self.assert_started()
        other = self.root / "other" / ".codex"
        self.env.update(HOME=str(other.parent), CODEX_HOME=str(other))
        self.assert_started()
        for name in ("config.toml", "auth.json"):
            replacement = self.shared / (name + ".tmp")
            expected = (self.shared / name).read_bytes() + b"\n"
            replacement.write_bytes(expected)
            replacement.replace(self.shared / name)
            for home in (self.codex, other):
                self.assertEqual((home / name).read_bytes(), expected)

    def test_missing_shared_file_fails_without_moving_private_file(self):
        for name in ("config.toml", "auth.json"):
            with self.subTest(name=name):
                shared = self.shared / name
                original = shared.read_bytes()
                shared.unlink()
                (self.codex / name).write_text("private")
                result = self.run_entrypoint()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(shared), result.stderr)
                self.assertEqual((self.codex / name).read_text(), "private")
                shared.write_bytes(original)

    def test_shared_mode_never_calls_codex_bootstrap(self):
        tools = self.root / "bin"
        tools.mkdir()
        for name, code in (("multica", 0), ("codex", 43)):
            tool = tools / name
            tool.write_text(f"#!/bin/sh\nexit {code}\n")
            tool.chmod(0o700)
        self.env.update(PATH=str(tools) + os.pathsep + os.defpath,
                        CODEX_BOOTSTRAP_API_KEY="INVALID-IGNORED-TEST-KEY")
        self.assert_started("multica", "daemon", "start")
        self.assertEqual((self.shared / "auth.json").read_bytes(), self.auth)


if __name__ == "__main__":
    unittest.main()
