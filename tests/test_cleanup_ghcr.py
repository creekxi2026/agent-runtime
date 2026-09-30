"""Safety tests use in-memory registry/API fixtures; no remote deletes."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cleanup_ghcr as cleanup

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def digest(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def descriptor(value):
    body = json.dumps(value).encode()
    return {"digest": "sha256:" + hashlib.sha256(body).hexdigest(), "size": len(body)}


class RegistryFixture:
    def __init__(self, arches=("amd64", "arm64"), nested=True):
        self.manifests = {}
        self.blobs = {}
        self.checked_blobs = []
        self.reads = 0
        self.race_at = None
        leaves = []
        for arch in (*arches, "unknown"):
            config = {"architecture": arch, "os": "linux" if arch != "unknown" else "unknown"}
            layer = {"fixture": arch}
            for value in (config, layer):
                self.blobs[descriptor(value)["digest"]] = value
            leaf = {"schemaVersion": 2, "mediaType": MANIFEST,
                    "config": descriptor(config), "layers": [descriptor(layer)]}
            leaves.append(self.add_manifest(leaf))
        if nested:
            leaves = [self.add_manifest({"schemaVersion": 2, "mediaType": INDEX, "manifests": leaves})]
        self.latest = self.add_manifest({"schemaVersion": 2, "mediaType": INDEX, "manifests": leaves})["digest"]

    def add_manifest(self, value):
        desc = descriptor(value)
        self.manifests[desc["digest"]] = value
        return desc

    def manifest(self, ref):
        if ref == "latest":
            self.reads += 1
            if self.race_at and self.reads >= self.race_at:
                return {}, digest("concurrent publication")
            ref = self.latest
        return self.manifests[ref], ref

    def blob(self, desc, *, config=False):
        self.checked_blobs.append(desc["digest"])
        return self.blobs[desc["digest"]] if config else self.blobs[desc["digest"]] and None


class PackagesFixture:
    def __init__(self, registry):
        self.items = [{"id": n, "name": value, "metadata": {"container": {"tags":
                      ["latest"] if value == registry.latest else []}}}
                      for n, value in enumerate(registry.manifests, 1)]
        self.items.extend([{"id": 100, "name": digest("old latest"),
                            "metadata": {"container": {"tags": ["0.1.0", "old-amd64"]}}},
                           {"id": 101, "name": digest("orphan build"),
                            "metadata": {"container": {"tags": []}}}])
        self.deleted = []

    def versions(self):
        return copy.deepcopy(self.items)

    def version(self, ident):
        return copy.deepcopy(next(item for item in self.items if item["id"] == ident))

    def delete(self, ident):
        self.deleted.append(ident)
        self.items = [item for item in self.items if item["id"] != ident]


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.registry = RegistryFixture()
        self.packages = PackagesFixture(self.registry)

    def run_cleanup(self, **kwargs):
        return cleanup.cleanup(self.registry, self.packages, **kwargs)

    def test_dry_run_default_retains_recursive_closure_and_checks_blobs(self):
        result = self.run_cleanup()
        self.assertEqual(result["delete_ids"], [100, 101])
        self.assertEqual(set(result["retain"]), set(self.registry.manifests))
        self.assertEqual(set(self.registry.checked_blobs), set(self.registry.blobs))
        self.assertEqual(self.packages.deleted, [])

    def test_expected_publication_mismatch_fails_before_deleting(self):
        with self.assertRaises(ValueError):
            self.run_cleanup(apply=True, expected_latest=digest("new publication"))
        self.assertEqual(self.packages.deleted, [])

    def test_apply_removes_only_history_and_orphans(self):
        self.run_cleanup(apply=True)
        self.assertEqual(self.packages.deleted, [100, 101])
        self.assertEqual({v["name"] for v in self.packages.items}, set(self.registry.manifests))

    def test_missing_or_duplicate_arch_fails_before_deleting(self):
        for arches in (("amd64",), ("amd64", "arm64", "amd64")):
            with self.subTest(arches=arches):
                registry = RegistryFixture(arches)
                packages = PackagesFixture(registry)
                with self.assertRaises(ValueError):
                    cleanup.cleanup(registry, packages, apply=True)
                self.assertEqual(packages.deleted, [])

    def test_missing_blob_fails_before_deleting(self):
        self.registry.blobs.pop(next(iter(self.registry.blobs)))
        with self.assertRaises(KeyError):
            self.run_cleanup(apply=True)
        self.assertEqual(self.packages.deleted, [])

    def test_retained_aliases_fail_closed_including_old_latest_alias(self):
        for position in (0, -3):
            with self.subTest(position=position):
                packages = PackagesFixture(self.registry)
                packages.items[position]["metadata"]["container"]["tags"].append("0.1.0")
                with self.assertRaises(ValueError):
                    cleanup.cleanup(self.registry, packages, apply=True)
                self.assertEqual(packages.deleted, [])

    def test_api_missing_dependency_or_latest_mismatch_fails_closed(self):
        for position in (0, -3):
            with self.subTest(position=position):
                packages = PackagesFixture(self.registry)
                packages.items.pop(position)
                with self.assertRaises(ValueError):
                    cleanup.cleanup(self.registry, packages, apply=True)
                self.assertEqual(packages.deleted, [])

    def test_latest_race_before_or_between_deletes_stops(self):
        for race_at, deleted in ((2, []), (3, []), (4, [100])):
            with self.subTest(race_at=race_at):
                registry = RegistryFixture()
                registry.race_at = race_at
                packages = PackagesFixture(registry)
                with self.assertRaises(ValueError):
                    cleanup.cleanup(registry, packages, apply=True)
                self.assertEqual(packages.deleted, deleted)

    def test_api_error_or_candidate_mutation_stops(self):
        with patch.object(self.packages, "versions", side_effect=RuntimeError("API denied")):
            with self.assertRaises(RuntimeError):
                self.run_cleanup(apply=True)
        changed = self.packages.version(100)
        changed["metadata"]["container"]["tags"] = ["latest"]
        with patch.object(self.packages, "version", return_value=changed):
            with self.assertRaises(ValueError):
                self.run_cleanup(apply=True)
        self.assertEqual(self.packages.deleted, [])

    def test_delete_permission_error_is_not_silenced(self):
        with patch.object(self.packages, "delete", side_effect=PermissionError("403")):
            with self.assertRaises(PermissionError):
                self.run_cleanup(apply=True)

    def test_post_delete_inventory_is_verified(self):
        with patch.object(self.packages, "delete", return_value=None):
            with self.assertRaises(ValueError):
                self.run_cleanup(apply=True)

    def test_manifest_digest_mismatch_fails_closed(self):
        original = self.registry.manifest
        def wrong(ref):
            body, value = original(ref)
            return body, digest("wrong") if ref != "latest" else value
        with patch.object(self.registry, "manifest", side_effect=wrong):
            with self.assertRaises(ValueError):
                self.run_cleanup(apply=True)
        self.assertEqual(self.packages.deleted, [])


class ApiTests(unittest.TestCase):
    def test_only_exact_repository_is_allowed(self):
        for repo in ("attacker/agent-runtime", "creekxi2026/another", "creekxi2026/agent-runtime/.."):
            with self.subTest(repo=repo), self.assertRaises(ValueError):
                cleanup.Packages(repo, "not-a-real-token")

    def test_pagination_reads_all_pages_and_rejects_duplicates(self):
        api = cleanup.Packages(cleanup.REPOSITORY, "not-a-real-token")
        first = [{"id": i} for i in range(1, 101)]
        with patch.object(api, "request", side_effect=[first, [{"id": 101}]]) as request:
            self.assertEqual(len(api.versions()), 101)
            self.assertEqual([c.args[1] for c in request.call_args_list],
                             [api.path + "/versions?per_page=100&page=1", api.path + "/versions?per_page=100&page=2"])
        with patch.object(api, "request", side_effect=[first, [{"id": 1}]]):
            with self.assertRaises(ValueError):
                api.versions()

    def test_pagination_failure_does_not_return_partial_inventory(self):
        api = cleanup.Packages(cleanup.REPOSITORY, "not-a-real-token")
        with patch.object(api, "request", side_effect=[[{"id": i} for i in range(100)], PermissionError("403")]):
            with self.assertRaises(PermissionError):
                api.versions()

    def test_package_must_be_linked_to_exact_repository(self):
        api = cleanup.Packages(cleanup.REPOSITORY, "not-a-real-token")
        with patch.object(api, "request", return_value={"name": "agent-runtime", "package_type": "container",
                                                       "repository": {"full_name": "other/repo"}}):
            with self.assertRaises(ValueError):
                api.verify_target()

    def test_blob_bytes_digest_and_size_are_checked(self):
        registry = object.__new__(cleanup.PublicRegistry)
        registry.repository = cleanup.REPOSITORY
        registry.token = "anonymous-fixture"
        value = {"os": "linux", "architecture": "amd64"}
        for body, size in ((b"broken", descriptor(value)["size"]),
                           (json.dumps(value).encode(), 1)):
            with self.subTest(body=body), patch("urllib.request.urlopen", return_value=io.BytesIO(body)):
                desc = {**descriptor(value), "size": size}
                with self.assertRaises(ValueError):
                    registry.blob(desc, config=True)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(value).encode())):
            self.assertEqual(registry.blob(descriptor(value), config=True), value)


if __name__ == "__main__":
    unittest.main()
