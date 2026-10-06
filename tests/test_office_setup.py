"""Private HOME lifecycle, real local probes, and failure-safe skill retirement."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "skills/agent-office-setup"
spec = importlib.util.spec_from_file_location("office_setup", SOURCE / "scripts/setup.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OfficeSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="office-setup-test-")
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name).resolve()
        self.home = self.temp / "home"
        self.skill = self.home / ".agents/skills/agent-office-setup"
        shutil.copytree(SOURCE, self.skill, ignore=shutil.ignore_patterns("__pycache__"))
        self.setup = module.Setup(self.home, self.skill)

    def test_status_is_read_only_and_basic_install_is_offline(self):
        self.assertEqual(self.setup.read()["phase"], "not_started")
        self.assertFalse(self.setup.root.exists())
        with patch.object(self.setup, "run", side_effect=AssertionError("No installs")):
            state = self.setup.install(["basic"])
        self.assertEqual(state["phase"], "installed")
        self.assertFalse((self.setup.root / "venv").exists())
        self.assertEqual(self.setup.verify()["verified_features"], ["basic"])

    def test_finish_keeps_data_and_other_skills_and_survives_entrypoint_restart(self):
        self.setup.install(["basic"])
        custom = self.skill.parent / "custom"
        custom.mkdir()
        (custom / "SKILL.md").write_text("custom")
        work = self.home / "workspace"
        work.mkdir()
        (work / "result.csv").write_text("keep")
        state = self.setup.finish("Delivered fixture CSV")
        self.assertEqual(state["phase"], "complete")
        self.assertFalse(self.skill.exists())
        self.assertEqual((work / "result.csv").read_text(), "keep")
        self.assertTrue((custom / "SKILL.md").exists())
        self.assertTrue((SOURCE / "SKILL.md").exists())
        # This entrypoint fixture receives its own HOME; no host profile is changed.
        env = {"HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex"),
               "PATH": "/usr/bin:/bin", "AGENT_MANAGED_SKILLS_DIR": str(self.temp / "managed")}
        subprocess.run(["sh", str(REPO / "entrypoint.sh"), "true"],
                       env=env, check=True, capture_output=True)
        self.assertEqual(self.setup.read(), state)
        self.assertFalse(self.skill.exists())

    def test_finish_without_successful_install_preserves_skill(self):
        with self.assertRaisesRegex(ValueError, "install"):
            self.setup.finish("A task")
        self.assertTrue(self.skill.exists())
        self.assertFalse(self.setup.state_file.exists())

    def test_failed_install_records_selection_and_can_resume(self):
        with patch.object(self.setup, "run", side_effect=subprocess.CalledProcessError(1, ["fixture"])):
            with self.assertRaises(subprocess.CalledProcessError):
                self.setup.install(["xlsx"])
        self.assertEqual(self.setup.read()["phase"], "installing")
        self.assertEqual(self.setup.read()["features"], ["xlsx"])
        self.assertTrue(self.skill.exists())
        with patch.object(self.setup, "run", return_value=subprocess.CompletedProcess([], 0, '[{"name":"fixture","version":"1"}]')):
            state = self.setup.install(["basic"])
        self.assertEqual(state["features"], ["basic", "xlsx"])
        self.assertEqual(state["phase"], "installed")

    def test_failed_reverification_cannot_retire_skill(self):
        self.setup.install(["basic"])
        self.setup.verify()
        with patch.object(self.setup, "run", side_effect=subprocess.CalledProcessError(1, ["fixture"])):
            with self.assertRaises(subprocess.CalledProcessError):
                self.setup.finish("A task")
        self.assertEqual(self.setup.read()["phase"], "installed")
        self.assertEqual(self.setup.read()["verified_features"], [])
        self.assertTrue(self.skill.exists())

    def test_task_evidence_required(self):
        self.setup.install(["basic"])
        with self.assertRaisesRegex(ValueError, "actual verified"):
            self.setup.finish(" ")
        self.assertTrue(self.skill.exists())

    def test_completion_write_failure_keeps_skill(self):
        self.setup.install(["basic"])
        state = self.setup.verify()
        with patch.object(self.setup, "verify", return_value=state), \
                patch.object(self.setup, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.setup.finish("A task")
        self.assertTrue(self.skill.exists())
        self.assertEqual(self.setup.read()["phase"], "verified")

    def test_cleanup_failure_preserves_completion_and_retry_only_retires(self):
        self.setup.install(["basic"])
        with patch.object(module.shutil, "rmtree", side_effect=OSError("fixture busy")):
            with self.assertRaises(OSError):
                self.setup.finish("Delivered fixture")
        self.assertEqual(self.setup.read()["phase"], "complete")
        with self.assertRaisesRegex(ValueError, "complete"):
            self.setup.install(["docx"])
        with patch.object(self.setup, "run", side_effect=AssertionError("No rerun")):
            state = self.setup.finish("Retry cleanup")
        self.assertEqual(state["verified_task"], "Delivered fixture")
        self.assertFalse(self.skill.exists())

    def test_source_copy_and_shared_symlink_cannot_be_deleted(self):
        source_setup = module.Setup(self.home, SOURCE)
        with self.assertRaisesRegex(ValueError, "private copy"):
            source_setup.finish("A task")
        shutil.rmtree(self.skill)
        self.skill.symlink_to(SOURCE)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.setup.finish("A task")
        self.assertTrue((SOURCE / "SKILL.md").exists())

    def test_linked_skill_parent_cannot_be_modified(self):
        shared = self.temp / "shared"
        self.skill.parent.rename(shared)
        self.skill.parent.symlink_to(shared)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.setup.install(["basic"])
        self.assertTrue((shared / "agent-office-setup/SKILL.md").exists())

    def test_linked_state_or_state_parent_cannot_be_modified(self):
        outside = self.temp / "outside.json"
        outside.write_text('{"keep": true}')
        self.setup.root.mkdir(parents=True)
        self.setup.state_file.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.setup.install(["basic"])
        self.assertEqual(outside.read_text(), '{"keep": true}')
        self.setup.state_file.unlink()
        shared = self.temp / "shared-state"
        self.setup.root.rename(shared)
        self.setup.root.symlink_to(shared)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.setup.install(["basic"])

    def test_corrupt_state_is_preserved(self):
        self.setup.root.mkdir(parents=True)
        for content in ("broken", "[]", '{"schema": 99}',
                        '{"schema": 1, "phase": [], "features": []}',
                        '{"schema": 1, "phase": "installed", "features": [[]]}'):
            self.setup.state_file.write_text(content)
            with self.assertRaises(ValueError):
                self.setup.install(["basic"])
            self.assertEqual(self.setup.state_file.read_text(), content)
        self.assertTrue(self.skill.exists())

    def test_concurrent_conversation_cannot_change_setup(self):
        other = module.Setup(self.home, self.skill)
        with self.setup.exclusive():
            with self.assertRaisesRegex(ValueError, "Another conversation"):
                with other.exclusive():
                    other.install(["basic"])
        self.assertEqual(other.read()["phase"], "not_started")
        with other.exclusive():
            other.install(["basic"])
        self.assertEqual(other.read()["phase"], "installed")

    def test_private_homes_complete_independently(self):
        other_home = self.temp / "other"
        other_skill = other_home / ".agents/skills/agent-office-setup"
        shutil.copytree(SOURCE, other_skill)
        other = module.Setup(other_home, other_skill)
        self.setup.install(["basic"])
        self.setup.finish("Delivered fixture")
        self.assertEqual(other.read()["phase"], "not_started")
        self.assertTrue(other_skill.exists())

    def test_cli_status_from_source_has_no_write_side_effect(self):
        # CLI parsing without touching any HOME state.
        with patch.object(sys, "argv", ["setup.py", "status"]), \
                patch.object(module.Path, "home", return_value=self.home), \
                patch("builtins.print") as output:
            module.main()
        self.assertEqual(json.loads(output.call_args.args[0])["phase"], "not_started")
        self.assertFalse(self.setup.root.exists())


if __name__ == "__main__":
    unittest.main()
