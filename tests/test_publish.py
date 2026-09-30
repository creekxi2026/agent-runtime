"""Exercise workflow's embedded validation code without pushing any images."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

WORKFLOW = (Path(__file__).resolve().parents[1] / ".github/workflows/publish.yml").read_text()
BLOCKS = [textwrap.dedent(block) for block in re.findall(
    r"^          python3 - <<'PY'\n(.*?)^          PY$", WORKFLOW, re.MULTILINE | re.DOTALL)]
CONFIG = "sha256:" + "a" * 64
AMD64 = "sha256:" + "b" * 64
ARM64 = "sha256:" + "c" * 64


class PublishTests(unittest.TestCase):
    def test_workflow_shell_syntax(self):
        # Every multiline run body is checked; expression values only occur in env.
        bodies = re.findall(r"^        run: \|\n((?:          .*\n|\n)+)", WORKFLOW, re.MULTILINE)
        self.assertEqual(len(bodies), 4)
        for body in bodies:
            subprocess.run(["bash", "-n"], input=textwrap.dedent(body), text=True, check=True)
        self.assertEqual(len(BLOCKS), 3)

    def test_push_only_uses_digest_exporter_and_latest_is_the_only_public_tag(self):
        self.assertIn("push-by-digest=true,name-canonical=true,push=true", WORKFLOW)
        self.assertIn("--provenance=false --sbom=false", WORKFLOW)
        self.assertNotIn("docker push", WORKFLOW)
        self.assertNotIn("docker tag", WORKFLOW)
        self.assertEqual(re.findall(r'-t "\$image:([^"\s]+)"', WORKFLOW), ["latest"])
        self.assertIn("mapfile -d '' -t args", WORKFLOW)
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertIn("needs.check.outputs.changed == 'false'", WORKFLOW)

    def test_only_matching_tested_config_creates_artifact(self):
        for config in (CONFIG, "sha256:" + "d" * 64):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                (folder / "push-metadata.json").write_text(json.dumps({
                    "containerimage.config.digest": config, "containerimage.digest": AMD64}))
                with patch.dict(os.environ, {"RUNNER_TEMP": tmp, "ARCH": "amd64"}), \
                        patch("subprocess.check_output", return_value=CONFIG + "\n"):
                    if config == CONFIG:
                        exec(compile(BLOCKS[0], "workflow-push", "exec"), {})
                        self.assertEqual((folder / "digests/amd64").read_text().strip(), AMD64)
                    else:
                        with self.assertRaises(SystemExit):
                            exec(compile(BLOCKS[0], "workflow-push", "exec"), {})
                        self.assertFalse((folder / "digests").exists())

    def test_combine_requires_exactly_two_distinct_valid_digests(self):
        cases = [({"amd64": AMD64, "arm64": ARM64}, True),
                 ({"amd64": AMD64}, False),
                 ({"amd64": AMD64, "arm64": ARM64, "extra": AMD64}, False),
                 ({"amd64": AMD64, "arm64": AMD64}, False),
                 ({"amd64": AMD64, "arm64": "latest"}, False)]
        for files, valid in cases:
            with self.subTest(files=files), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp) / "digests"
                folder.mkdir()
                for name, value in files.items():
                    (folder / name).write_text(value + "\n")
                with patch.dict(os.environ, {"RUNNER_TEMP": tmp}):
                    if valid:
                        exec(compile(BLOCKS[1], "workflow-combine", "exec"), {})
                    else:
                        with self.assertRaises(SystemExit):
                            exec(compile(BLOCKS[1], "workflow-combine", "exec"), {})


if __name__ == "__main__":
    unittest.main()
