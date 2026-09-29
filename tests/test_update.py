"""Unit fixtures only: registry behavior is additionally exercised against GHCR."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
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


if __name__ == "__main__":
    unittest.main()
