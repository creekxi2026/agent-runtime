"""Unit fixtures only: registry behavior is additionally exercised against GHCR."""
from contextlib import redirect_stdout
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_upstream
from check_upstream import platforms, requires_build

BASE = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
REVISION = "c" * 40


def labels(base=BASE, revision=REVISION):
    return {arch: {
        "org.opencontainers.image.base.digest": base,
        "org.opencontainers.image.revision": revision,
    } for arch in ("amd64", "arm64")}


class UpdateTests(unittest.TestCase):
    def test_unchanged_skips(self):
        self.assertFalse(requires_build(BASE, REVISION, labels()))

    def test_new_upstream_or_source_builds(self):
        self.assertTrue(requires_build(OTHER, REVISION, labels()))
        self.assertTrue(requires_build(BASE, "d" * 40, labels()))

    def test_old_or_partial_publication_builds(self):
        self.assertTrue(requires_build(BASE, REVISION, {}))
        self.assertTrue(requires_build(BASE, REVISION, {"amd64": labels()["amd64"]}))
        mixed = labels()
        mixed["arm64"] = labels(OTHER)["arm64"]
        self.assertTrue(requires_build(BASE, REVISION, mixed))

    def test_dependency_change_rebuilds_and_unchanged_skips(self):
        current = labels()
        for item in current.values():
            item["io.creek.runtime.dependencies"] = BASE
        self.assertFalse(requires_build(BASE, REVISION, current, BASE))
        self.assertTrue(requires_build(BASE, REVISION, current, OTHER))
        self.assertTrue(requires_build(BASE, REVISION, labels(), BASE))
        with self.assertRaises(ValueError):
            requires_build(BASE, REVISION, current, "latest")

    def test_missing_architecture_rejected(self):
        with self.assertRaises(ValueError):
            platforms({"manifests": []})

    def test_multiarch_with_attestation(self):
        manifests = [{"digest": BASE, "platform": {"os": "linux", "architecture": a}}
                     for a in ("amd64", "arm64", "unknown")]
        self.assertEqual(set(platforms({"manifests": manifests})), {"amd64", "arm64"})
        with self.assertRaises(ValueError):
            platforms({"manifests": manifests + [manifests[0]]})

    def test_malformed_digest_rejected(self):
        with self.assertRaises(ValueError):
            requires_build("latest", REVISION, {})


class MainUpdateTests(unittest.TestCase):
    INDEX = "application/vnd.oci.image.index.v1+json"
    MANIFEST = "application/vnd.oci.image.manifest.v1+json"
    CONFIG = "application/vnd.oci.image.config.v1+json"

    def run_main(self, published, error=None, extra_routes=None):
        """Run real main/Registry with local JSON and mocked HTTP only."""
        lock_bytes = json.dumps({"base_digest": BASE, "base_image": "base@" + BASE}).encode()
        dependency = "sha256:" + hashlib.sha256(lock_bytes).hexdigest()
        routes = {
            "https://ghcr.io/token?scope=repository:owner/runtime:pull": {"token": "fixture"},
            "https://ghcr.io/v2/owner/runtime/manifests/latest": published,
            "https://ghcr.io/v2/owner/runtime/manifests/" + BASE: {
                "schemaVersion": 2, "mediaType": self.MANIFEST,
                "config": {"digest": OTHER, "size": 1, "mediaType": self.CONFIG}, "layers": [],
            },
            "https://ghcr.io/v2/owner/runtime/blobs/" + OTHER: {
                "os": "linux", "architecture": "amd64",
                "config": {"Labels": {**labels()["amd64"], "io.creek.runtime.dependencies": dependency}},
            },
        }
        routes.update(extra_routes or {})

        def transport(request, timeout):
            url = request if isinstance(request, str) else request.full_url
            if url.endswith("/manifests/latest") and error is not None:
                raise error
            self.assertIn(url, routes, "Unexpected HTTP request")
            response = routes[url]
            if isinstance(response, Exception):
                raise response
            return io.BytesIO(response if isinstance(response, bytes) else json.dumps(response).encode())

        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                Path("runtime-deps.json").write_bytes(lock_bytes)
                Path("VERSION").write_text("1.0.0\n")
                output = Path(directory) / "github-output"
                env = {"GITHUB_SHA": REVISION, "GITHUB_REPOSITORY": "owner/runtime",
                       "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_OUTPUT": str(output)}
                with patch.dict(os.environ, env, clear=True), \
                     patch.object(check_upstream.subprocess, "run") as resolve, \
                     patch.object(check_upstream.urllib.request, "urlopen", side_effect=transport), \
                     redirect_stdout(io.StringIO()) as stdout:
                    try:
                        check_upstream.main()
                    except Exception:
                        self.assertFalse(output.exists(), "Failure must not emit build outputs")
                        raise
                resolve.assert_called_once_with(
                    [sys.executable, "scripts/resolve_tools.py", "--output", "runtime-deps.json"], check=True)
                result = json.loads(stdout.getvalue())
                self.assertIn("changed=" + result["changed"] + "\n", output.read_text())
                return result
            finally:
                os.chdir(previous)

    def index(self, arches):
        return {"schemaVersion": 2, "mediaType": self.INDEX, "manifests": [
            {"digest": BASE, "size": 1, "mediaType": self.MANIFEST,
             "platform": {"os": "linux", "architecture": arch}} for arch in arches
        ]}

    def test_main_missing_architecture_rebuilds(self):
        for arches in (["amd64"], ["arm64"], []):
            with self.subTest(arches=arches):
                self.assertEqual(self.run_main(self.index(arches))["changed"], "true")

    def test_main_single_architecture_manifest_rebuilds(self):
        for media_type, config_type in (
            (self.MANIFEST, self.CONFIG),
            ("application/vnd.docker.distribution.manifest.v2+json",
             "application/vnd.docker.container.image.v1+json"),
        ):
            with self.subTest(media_type=media_type):
                manifest = {"schemaVersion": 2, "mediaType": media_type,
                            "config": {"digest": OTHER, "size": 1, "mediaType": config_type}, "layers": []}
                self.assertEqual(self.run_main(manifest)["changed"], "true")

    def test_main_invalid_publication_is_not_rebuild(self):
        bad_digest = self.index(["amd64"])
        bad_digest["manifests"][0]["digest"] = "latest"
        bad_ignored_digest = self.index(["unknown"])
        bad_ignored_digest["manifests"][0]["digest"] = "latest"
        bad_platform = self.index(["arm64"])
        bad_platform["manifests"][0]["platform"]["os"] = 42
        bad_schema = self.index([])
        bad_schema["schemaVersion"] = 1
        invalid = [None, {}, {"schemaVersion": 2, "mediaType": "unknown", "manifests": []},
                   {"schemaVersion": 2, "mediaType": self.INDEX},
                   {"schemaVersion": 2, "mediaType": self.MANIFEST},
                   bad_schema, bad_digest, bad_ignored_digest, bad_platform,
                   self.index(["amd64", "amd64"])]
        for published in invalid:
            with self.subTest(published=published):
                with self.assertRaises((ValueError, KeyError, TypeError, AttributeError)):
                    self.run_main(published)

    def test_main_corrupt_single_manifest_is_not_rebuild(self):
        manifest = {"schemaVersion": 2, "mediaType": self.MANIFEST,
                    "config": {"digest": OTHER, "size": 1, "mediaType": self.CONFIG}, "layers": []}
        for config in ({}, {"os": "linux", "architecture": "amd64", "config": {"Labels": []}}):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    self.run_main(manifest, extra_routes={
                        "https://ghcr.io/v2/owner/runtime/blobs/" + OTHER: config})
        for layers in (None, [{"digest": "latest", "size": 1, "mediaType": "layer"}]):
            with self.subTest(layers=layers):
                with self.assertRaises((ValueError, TypeError)):
                    self.run_main({**manifest, "layers": layers})

    def test_main_complete_matching_index_skips(self):
        for media_type in (self.INDEX, "application/vnd.docker.distribution.manifest.list.v2+json"):
            with self.subTest(media_type=media_type):
                index = self.index(["amd64", "arm64", "unknown"])
                index["mediaType"] = media_type
                self.assertEqual(self.run_main(index)["changed"], "false")

    def test_main_missing_latest_rebuilds(self):
        error = urllib.error.HTTPError("https://ghcr.io/v2/owner/runtime/manifests/latest",
                                       404, "Not Found", Message(), None)
        self.assertEqual(self.run_main(None, error=error)["changed"], "true")

    def test_main_transport_failures_are_not_rebuild(self):
        url = "https://ghcr.io/v2/owner/runtime/manifests/latest"
        errors = [urllib.error.HTTPError(url, code, "fixture", Message(), None)
                  for code in (403, 429, 500)] + [urllib.error.URLError("fixture network failure")]
        for error in errors:
            with self.subTest(error=error):
                with self.assertRaises(type(error)) as raised:
                    self.run_main(None, error=error)
                self.assertIs(raised.exception, error)

    def test_main_partial_child_fetch_failure_is_not_rebuild(self):
        url = "https://ghcr.io/v2/owner/runtime/blobs/" + OTHER
        error = urllib.error.HTTPError(url, 401, "Unauthorized", Message(), None)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.run_main(self.index(["amd64"]), extra_routes={url: error})
        self.assertIs(raised.exception, error)

    def test_main_malformed_json_is_not_rebuild(self):
        with self.assertRaises(json.JSONDecodeError):
            self.run_main(None, extra_routes={
                "https://ghcr.io/v2/owner/runtime/manifests/latest": b"not JSON"})

    def test_main_authentication_error_is_not_rebuild(self):
        error = urllib.error.HTTPError("https://ghcr.io/v2/owner/runtime/manifests/latest",
                                       401, "Unauthorized", Message(), None)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.run_main(None, error=error)
        self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()
