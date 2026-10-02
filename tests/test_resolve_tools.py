"""Offline contracts for the official two-platform dependency manifest."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = load("resolve_tools")

    def test_formal_versions_only(self):
        self.assertEqual(self.r.stable_version("v26.10.0"), "26.10.0")
        for value in ("1.2.3-beta.1", "v1.2.3+build", "latest", "01.2.3", "1.2.3\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.r.stable_version(value)

    def test_platform_index_requires_exactly_both_linux_arches(self):
        entries = [{"platform": {"os": "linux", "architecture": arch},
                    "digest": "sha256:" + digit * 64}
                   for arch, digit in (("amd64", "a"), ("arm64", "b"))]
        self.assertEqual(set(self.r.platforms({"manifests": entries})), {"amd64", "arm64"})
        for invalid in (entries[:1], entries + entries[:1]):
            with self.assertRaises(ValueError):
                self.r.platforms({"manifests": invalid})

    def test_platform_digest_is_validated_not_repaired(self):
        with self.assertRaises(ValueError):
            self.r.digest("SHA256:" + "a" * 64)
        with self.assertRaises(ValueError):
            self.r.digest("sha256:" + "a" * 64 + "\n")

    def test_sha256_requires_exact_checksum_filename(self):
        text = "a" * 64 + "  node.tar.xz\n" + "b" * 64 + "  other.tar.xz\n"
        self.assertEqual(self.r.checksum(text, "node.tar.xz"), "a" * 64)
        with self.assertRaises(ValueError):
            self.r.checksum(text, "missing.tar.xz")
        with self.assertRaises(ValueError):
            self.r.checksum(text + text, "node.tar.xz")

    def test_github_release_rejects_draft_and_prerelease(self):
        for field in ("draft", "prerelease"):
            with patch.object(self.r, "github_json", return_value={"tag_name": "v1.2.3", field: True}):
                with self.assertRaises(ValueError):
                    self.r.release("cli/cli")

    def test_release_artifact_requires_official_url_and_sha256(self):
        asset = {"name": "tool.tar.gz", "digest": "sha256:" + "a" * 64,
                 "browser_download_url": "https://github.com/cli/cli/releases/download/v1.2.3/tool.tar.gz"}
        data = {"tag_name": "v1.2.3", "assets": [asset]}
        self.assertEqual(self.r.release_artifact("cli/cli", data, "tool.tar.gz")["sha256"], "a" * 64)
        for key, value in (("digest", None), ("browser_download_url", "https://example.com/tool.tar.gz")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.r.release_artifact("cli/cli", {**data, "assets": [{**asset, key: value}]}, "tool.tar.gz")

    def test_serialization_is_byte_deterministic(self):
        a = {"versions": {"node": "26.10.0", "go": "1.27.1"}, "schema_version": 1}
        b = {"schema_version": 1, "versions": {"go": "1.27.1", "node": "26.10.0"}}
        self.assertEqual(self.r.serialize(a), self.r.serialize(b))
        self.assertTrue(self.r.serialize(a).endswith("\n"))

    def test_credentials_only_go_to_github_api(self):
        with patch.dict("os.environ", {"GH_TOKEN": "test-secret"}, clear=True):
            self.assertEqual(self.r.github_headers("https://api.github.com/repos/a/b")["Authorization"], "Bearer test-secret")
            for url in ("https://github.com/a/b", "https://api.github.com.evil.test/a", "http://api.github.com/a"):
                self.assertNotIn("Authorization", self.r.github_headers(url))

    def test_cross_origin_redirect_never_forwards_authorization(self):
        request = urllib.request.Request("https://api.github.com/a", headers={"Authorization": "Bearer sentinel"})
        redirected = self.r.SafeRedirect().redirect_request(request, None, 302, "Found", {}, "https://example.com/b")
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_npm_latest_rejects_prerelease_and_unofficial_tarball(self):
        data = {"name": "pnpm", "version": "12.8.1", "dist": {
            "tarball": "https://registry.npmjs.org/pnpm/-/pnpm-12.8.1.tgz",
            "integrity": "sha512-" + base64.b64encode(b"a" * 64).decode()}}
        with patch.object(self.r, "get_json", return_value=data):
            self.assertEqual(self.r.npm_package("pnpm")["version"], "12.8.1")
        for bad in ({**data, "version": "13.0.0-rc.1"}, {**data, "dist": {**data["dist"], "tarball": "https://evil.test/x"}}):
            with patch.object(self.r, "get_json", return_value=bad), self.assertRaises(ValueError):
                self.r.npm_package("pnpm")


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.i = load("install_tools")

    def test_hash_mismatch_fails_before_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tool.tar.gz"
            path.write_bytes(b"artifact")
            self.i.verify_file(path, "sha256", hashlib.sha256(b"artifact").hexdigest())
            with self.assertRaises(ValueError):
                self.i.verify_file(path, "sha256", "0" * 64)

    def test_npm_integrity_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tool.tgz"
            path.write_bytes(b"npm artifact")
            integrity = "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
            self.i.verify_integrity(path, integrity)
            with self.assertRaises(ValueError):
                self.i.verify_integrity(path, "sha512-" + base64.b64encode(b"0" * 64).decode())

    def test_extract_rejects_archive_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "tool.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                item = tarfile.TarInfo("../escaped")
                item.size = 1
                tar.addfile(item, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                self.i.extract(archive, Path(tmp) / "destination")
            self.assertFalse((Path(tmp) / "escaped").exists())

    def test_extract_does_not_preserve_world_writable_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "tool.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                item = tarfile.TarInfo("tool")
                item.size = 1
                item.mode = 0o777
                tar.addfile(item, io.BytesIO(b"x"))
            dest = Path(tmp) / "out"
            self.i.extract(archive, dest)
            self.assertEqual((dest / "tool").stat().st_mode & 0o022, 0)

    def test_npm_plan_pins_direct_tools_without_overriding_internal_contracts(self):
        manifest = {"npm": {name: {"version": version} for name, version in {
            "@playwright/cli": "0.1.22", "playwright": "1.63.0", "pnpm": "12.8.1"}.items()}}
        plan = self.i.npm_plan(manifest)
        self.assertEqual(plan["dependencies"]["playwright"], "1.63.0")
        self.assertNotIn("overrides", plan)
        self.assertEqual(plan["dependencies"]["pnpm"], "12.8.1")

    def test_version_check_rejects_wrong_executable_version(self):
        with patch.object(self.i.subprocess, "check_output", return_value="multica version 0.6.1\n"):
            self.i.check_version(["multica", "--version"], "0.6.1")
            with self.assertRaises(ValueError):
                self.i.check_version(["multica", "--version"], "0.6.0")


if __name__ == "__main__":
    unittest.main()
