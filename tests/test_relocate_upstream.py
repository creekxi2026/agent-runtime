"""Build-time relocation tests: temporary fixtures only, no Docker or real HOME."""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import relocate_upstream as relocation


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "home" / "agent"
        self.target = self.root / "opt" / "agent-upstream"
        self.source.mkdir(parents=True)
        self.owners = dict(home_uid=os.getuid(), home_gid=os.getgid(),
                           tool_uid=os.getuid(), tool_gid=os.getgid())

    def put(self, name, data, mode=0o644):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        path.chmod(mode)
        return path

    def run_relocation(self, **kwargs):
        relocation.relocate(self.source, self.target, **self.owners, **kwargs)

    def test_overlay_exdev_copy_preserves_internal_hardlinks_and_symlinks(self):
        import errno
        first = self.put("bin/one", "#!/bin/sh\n# " + str(self.source) + "/payload\n", 0o755)
        os.link(first, self.source / "bin/two")
        (self.source / "alias").symlink_to(first)
        with patch.object(relocation.os, "rename", side_effect=OSError(errno.EXDEV, "overlay fixture")):
            self.run_relocation()
        self.assertEqual(list(self.source.iterdir()), [])
        self.assertEqual((self.target / "bin/one").stat().st_ino, (self.target / "bin/two").stat().st_ino)
        self.assertIn(str(self.target), (self.target / "bin/one").read_text())
        self.assertEqual(os.readlink(self.target / "alias"), str(self.target / "bin/one"))

    def test_failed_overlay_copy_does_not_remove_source(self):
        import errno
        source = self.put("payload", "preserve me")
        with patch.object(relocation.os, "rename", side_effect=OSError(errno.EXDEV, "overlay fixture")), \
                patch.object(relocation.shutil, "copy2", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                self.run_relocation()
        self.assertEqual(source.read_text(), "preserve me")

    def test_move_preserves_unknown_cache_and_recreates_empty_home(self):
        self.put(".mystery/cache/payload", b"unchanged\x00bytes")
        self.run_relocation()
        self.assertEqual(list(self.source.iterdir()), [])
        self.assertEqual((self.target / ".mystery/cache/payload").read_bytes(),
                         b"unchanged\x00bytes")
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o755)
        self.assertEqual(self.source.stat().st_uid, os.getuid())
        self.assertEqual(self.source.stat().st_gid, os.getgid())

    def test_absolute_relative_external_and_directory_symlinks(self):
        self.put("pkg/tool", "tool")
        external = self.root / "external"
        external.mkdir()
        outside = external / "keep"
        outside.write_text(str(self.source) + "/must-not-change")
        links = {"absolute": str(self.source / "pkg/tool"),
                 "relative": "pkg/tool", "directory": str(self.source / "pkg"),
                 "home": str(self.source), "dangling": str(self.source / "missing"),
                 "external": str(external), "similar": str(self.source) + "-other"}
        for name, value in links.items():
            (self.source / name).symlink_to(value)
        self.run_relocation()
        for name, original in links.items():
            expected = original
            if original == str(self.source) or original.startswith(str(self.source) + "/"):
                expected = str(self.target) + original[len(str(self.source)):]
            self.assertEqual(os.readlink(self.target / name), expected)
        self.assertEqual(outside.read_text(), str(self.source) + "/must-not-change")
        self.assertTrue((self.target / "directory/tool").is_file())
        self.assertTrue((self.target / "relative").is_file())

    def test_venv_receipt_pth_activation_and_pnpm_shims(self):
        examples = {
            ".local/venv/bin/tool": f"#!{self.source}/.local/venv/bin/python\nprint('ok')\n",
            ".local/venv/pyvenv.cfg": f"home = {self.source}/.local/python/bin\n",
            ".local/venv/site/custom.pth": f"{self.source}/.local/lib\n",
            ".local/venv/uv-receipt.toml": f'python = "{self.source}/.local/python"\n',
            ".local/venv/bin/activate": f'VIRTUAL_ENV="{self.source}/.local/venv"\n',
            ".bashrc": f'export TOOL_HOME="${{TOOL_HOME:-{self.source}/.local}}"\n',
            ".local/share/pnpm/pnpm": f'#!/bin/sh\nexec "{self.source}/node/bin/node" "$@"\n',
            ".config/tool.json": f'{{"root": "{self.source}", "other": "{self.source}-old"}}\n',
        }
        for name, text in examples.items():
            self.put(name, text, 0o755)
        self.run_relocation()
        for name, text in examples.items():
            expected = text.replace(str(self.source), str(self.target))
            expected = expected.replace(str(self.target) + "-old", str(self.source) + "-old")
            self.assertEqual((self.target / name).read_text(), expected)
            self.assertEqual(stat.S_IMODE((self.target / name).stat().st_mode), 0o755)

    def test_playwright_registration_rewrites_contents_and_sha1_filename(self):
        package = str(self.source / ".local/pnpm/node_modules/playwright-core")
        old_hash = hashlib.sha1(package.encode()).hexdigest()
        self.put(".cache/ms-playwright/.links/" + old_hash, package)
        self.put(".cache/ms-playwright/chromium-123/chrome", b"\x7fELF\x00chrome")
        external = str(self.root / "external-playwright")
        external_hash = hashlib.sha1(external.encode()).hexdigest()
        self.put(".cache/ms-playwright/.links/" + external_hash, external)
        self.run_relocation()
        updated = package.replace(str(self.source), str(self.target), 1)
        new_hash = hashlib.sha1(updated.encode()).hexdigest()
        links = self.target / ".cache/ms-playwright/.links"
        self.assertEqual(sorted(p.name for p in links.iterdir()), sorted([new_hash, external_hash]))
        self.assertEqual((links / new_hash).read_text(), updated)
        self.assertEqual((links / external_hash).read_text(), external)
        self.assertTrue((self.target / ".cache/ms-playwright/chromium-123/chrome").is_file())

    def test_binary_and_pyc_are_unchanged(self):
        samples = {"go": b"\x7fELF\x00" + str(self.source).encode() + b"/runtime",
                   "blob": b"\x89\xff" + str(self.source).encode(),
                   "thing.pyc": str(self.source).encode() + b"/debug.py"}
        for name, data in samples.items():
            self.put(name, data)
        self.run_relocation()
        for name, data in samples.items():
            self.assertEqual((self.target / name).read_bytes(), data)

    def test_large_binary_is_not_read_in_full(self):
        binary = self.put("chromium", b"\x7fELF\x00" + str(self.source).encode())
        with binary.open("ab") as stream:
            stream.truncate(64 * 1024 * 1024)
        real_open = Path.open
        reads = []

        class WatchedFile:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def read(self, size=-1):
                reads.append(size)
                if size < 0 or size > 8192:
                    raise AssertionError("large binary read")
                return self.stream.read(size)

        def watched(path, *args, **kwargs):
            stream = real_open(path, *args, **kwargs)
            return WatchedFile(stream) if path.name == "chromium" else stream

        with patch.object(Path, "open", watched):
            self.run_relocation()
        self.assertTrue(reads)
        self.assertLessEqual(sum(reads), 16384)
        self.assertEqual((self.target / "chromium").stat().st_size, 64 * 1024 * 1024)

    def test_runtime_file_with_unsupported_encoding_fails(self):
        self.put("venv/pyvenv.cfg", (str(self.source) + "/python").encode("utf-16"))
        with self.assertRaisesRegex(ValueError, "text|encoding|binary"):
            self.run_relocation()

    def test_oversized_text_with_old_prefix_fails_instead_of_silently_skipping(self):
        self.put("large-script", "#!/bin/sh\n" + "# padding\n" * 100 + str(self.source) + "/bin/tool\n")
        with self.assertRaisesRegex(ValueError, "large|limit|size"):
            self.run_relocation(max_text_bytes=128)

    def test_playwright_invalid_record_is_rejected(self):
        self.put(".cache/ms-playwright/.links/invalid", "not an absolute package path")
        with self.assertRaisesRegex(ValueError, "Playwright|registration"):
            self.run_relocation()

    def test_existing_destination_and_overlapping_roots_are_rejected_before_move(self):
        payload = self.put("keep", "intact")
        self.target.mkdir(parents=True)
        with self.assertRaises((ValueError, FileExistsError)):
            self.run_relocation()
        self.assertEqual(payload.read_text(), "intact")
        self.target.rmdir()
        for target in [self.source, self.source / "nested", self.source.parent]:
            with self.subTest(target=target):
                with self.assertRaises((ValueError, FileExistsError)):
                    relocation.relocate(self.source, target, **self.owners)
                self.assertEqual(payload.read_text(), "intact")

    def test_symlink_destination_is_rejected(self):
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(self.root / "missing")
        with self.assertRaises((ValueError, FileExistsError)):
            self.run_relocation()
        self.assertTrue(self.source.is_dir())
        self.assertTrue(self.target.is_symlink())

    def test_symlink_source_is_rejected(self):
        actual = self.root / "actual"
        self.source.rename(actual)
        self.source.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_relocation()
        self.assertTrue(self.source.is_symlink())
        self.assertFalse(self.target.exists())

    def test_tool_permissions_cannot_be_modified_by_group_or_other(self):
        self.put("bin/tool", "#!/bin/sh\nexit 0\n", 0o777)
        (self.source / "bin").chmod(0o777)
        self.run_relocation()
        for path in [self.target, self.target / "bin", self.target / "bin/tool"]:
            self.assertEqual(path.stat().st_uid, os.getuid())
            self.assertEqual(path.stat().st_gid, os.getgid())
            self.assertFalse(path.stat().st_mode & 0o022)
        self.assertTrue(os.access(self.target / "bin/tool", os.X_OK))

    def test_private_home_becomes_readable_and_traversable_by_uid_1000(self):
        self.source.chmod(0o700)
        self.put("private/config", "config", 0o600)
        self.put("private/tool", "#!/bin/sh\nexit 0\n", 0o700)
        (self.source / "private").chmod(0o700)
        with patch.object(os, "chown") as chown:
            relocation.relocate(self.source, self.target, skip_chown=True)
            chown.assert_not_called()
        # Production owner is root, so UID 1000 needs these OTHER mode bits.
        for path in [self.target, self.target / "private", self.target / "private/tool"]:
            self.assertEqual(path.stat().st_mode & 0o005, 0o005)
        self.assertEqual(stat.S_IMODE((self.target / "private/config").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o755)

    def test_default_chown_uses_root_tools_and_1000_home_without_following_links(self):
        self.put("tool", "tool")
        (self.source / "outside").symlink_to("/does-not-exist")
        with patch.object(os, "chown") as chown:
            relocation.relocate(self.source, self.target)
        expected = {self.target: (0, 0), self.target / "tool": (0, 0), self.source: (1000, 1000)}
        self.assertEqual(len(chown.call_args_list), len(expected))
        for call in chown.call_args_list:
            path, uid, gid = call.args
            self.assertEqual((uid, gid), expected[path])
            self.assertEqual(call.kwargs, {"follow_symlinks": False})

    def test_external_relative_link_fails_instead_of_changing_resolution(self):
        (self.source / "external-relative").symlink_to("../external")
        with self.assertRaisesRegex(ValueError, "relative link escapes"):
            self.run_relocation()

    def test_file_url_and_nonprefix_paths(self):
        text = f'url = "file://{self.source}/pkg"\nother = "/else{self.source}/keep"\n'
        self.put("config.toml", text)
        self.run_relocation()
        self.assertEqual((self.target / "config.toml").read_text(),
                         text.replace(f"file://{self.source}", f"file://{self.target}"))

    def test_playwright_target_name_collision_fails(self):
        for root in [self.source, self.target]:
            package = str(root / "playwright")
            name = hashlib.sha1(package.encode()).hexdigest()
            self.put(".cache/ms-playwright/.links/" + name, package)
        with self.assertRaisesRegex(ValueError, "collision"):
            self.run_relocation()

    def test_special_files_fail_without_opening_them(self):
        os.mkfifo(self.source / "pipe")
        with self.assertRaisesRegex(ValueError, "special|unsupported"):
            self.run_relocation()

    def test_relocated_venv_console_script_really_executes(self):
        venv = self.source / ".local" / "venv"
        created = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                                 capture_output=True, text=True)
        self.assertEqual(created.returncode, 0, created.stderr)
        self.put(".local/venv/bin/probe", f"#!{venv}/bin/python\nimport sys\nprint(sys.prefix)\n", 0o700)
        self.run_relocation()
        result = subprocess.run([str(self.target / ".local/venv/bin/probe")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()).resolve(), (self.target / ".local/venv").resolve())

    def test_residual_runtime_reference_is_rejected_by_validation(self):
        self.put("tool.sh", f"#!{self.source}/bin/python\n")
        rewrite = relocation._text

        def skip_initial_write(*args, **kwargs):
            if kwargs.get("validate"):
                return rewrite(*args, **kwargs)

        with patch.object(relocation, "_text", skip_initial_write):
            with self.assertRaisesRegex(ValueError, "residual old runtime prefix"):
                self.run_relocation()
        self.assertFalse(self.source.exists())

    def test_directory_walk_errors_are_not_silently_ignored(self):
        self.put("keep", "contents")

        def denied(*args, **kwargs):
            callback = kwargs.get("onerror")
            if callback:
                callback(PermissionError("unreadable subtree"))
            return iter(())

        with patch.object(os, "walk", denied):
            with self.assertRaisesRegex(PermissionError, "unreadable subtree"):
                self.run_relocation()

    def test_cli_accepts_explicit_nonroot_owner_options(self):
        self.put("tool", f"#!{self.source}/bin/python\n")
        command = [sys.executable, str(Path(relocation.__file__)),
                   "--source", str(self.source), "--destination", str(self.target)]
        for name, value in self.owners.items():
            command.extend(["--" + name.replace("_", "-"), str(value)])
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.source.iterdir()), [])
        self.assertEqual((self.target / "tool").read_text(), f"#!{self.target}/bin/python\n")


if __name__ == "__main__":
    unittest.main()
