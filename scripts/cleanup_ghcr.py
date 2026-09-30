"""GHCR latest-only retention. Dry-run by default; credentials stay in the environment.

Only creekxi2026/agent-runtime is supported. Every manifest and blob reachable
from anonymous latest must be readable and digest-verified before any deletion.
The workflow serializes publishers; latest is also rechecked before each delete.
GHCR offers no atomic compare-and-delete, so other publishers must use that lock.
"""
import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request

from check_upstream import ARCHES, Registry, digest

REPOSITORY = "creekxi2026/agent-runtime"
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}


class PublicRegistry(Registry):
    """Uses only an anonymous pull token, never GITHUB_TOKEN."""

    def manifest(self, ref):
        return self.get("manifests/" + (ref if ref == "latest" else digest(ref)))

    def blob(self, desc, *, config=False):
        expected = digest(desc["digest"])
        if type(desc["size"]) is not int or desc["size"] < 0:
            raise ValueError("Invalid blob size")
        request = urllib.request.Request(
            "https://ghcr.io/v2/" + self.repository + "/blobs/" + expected,
            headers={"Authorization": "Bearer " + self.token},
        )
        sha = hashlib.sha256()
        size = 0
        content = bytearray()
        with urllib.request.urlopen(request, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                sha.update(chunk)
                size += len(chunk)
                if config:
                    if size > 16 * 1024 * 1024:
                        raise ValueError("Image config too large")
                    content.extend(chunk)
        if "sha256:" + sha.hexdigest() != expected or size != desc["size"]:
            raise ValueError("Blob digest/size mismatch: " + expected)
        return json.loads(content) if config else None


class Packages:
    def __init__(self, repository, token):
        if repository != REPOSITORY:
            raise ValueError("Cleanup is restricted to " + REPOSITORY)
        if not token:
            raise ValueError("GITHUB_TOKEN is required (package admin/delete access)")
        self.token = token
        self.path = "/users/creekxi2026/packages/container/agent-runtime"

    def request(self, method, path, *, allow_missing=False):
        if path != self.path and not path.startswith(self.path + "/versions"):
            raise ValueError("Out-of-scope package request")
        request = urllib.request.Request(
            "https://api.github.com" + path, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            if error.code == 404 and allow_missing:
                return None
            # No API error, including missing admin/delete permission, is ignored.
            raise RuntimeError(f"GitHub packages {method} failed (HTTP {error.code}); "
                               "check package admin access for this repository's GITHUB_TOKEN") from None

    def verify_target(self):
        package = self.request("GET", self.path)
        if (not isinstance(package, dict)
                or package["name"] != "agent-runtime" or package["package_type"] != "container"
                or package.get("repository", {}).get("full_name") != REPOSITORY):
            raise ValueError("Package is not linked to the exact expected repository")

    def versions(self):
        result = []
        seen = set()
        page = 1
        while True:
            batch = self.request("GET", f"{self.path}/versions?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ValueError("Invalid package versions response")
            for item in batch:
                if item["id"] in seen:
                    raise ValueError("Duplicate version across pages; refusing unstable inventory")
                seen.add(item["id"])
                result.append(item)
            if len(batch) < 100:
                return result
            page += 1

    def version(self, ident):
        return self.request("GET", f"{self.path}/versions/{ident}")

    def delete(self, ident):
        if type(ident) is not int or ident <= 0:
            raise ValueError("Invalid package version id")
        path = f"{self.path}/versions/{ident}"
        self.request("DELETE", path)
        if self.request("GET", path, allow_missing=True) is not None:
            raise ValueError("Deleted version still visible: " + str(ident))


def manifest_closure(registry, expected_latest=None):
    root, latest = registry.manifest("latest")
    digest(latest)
    if expected_latest is not None and latest != digest(expected_latest):
        raise ValueError("Anonymous latest differs from the just-published index")
    retained = set()
    active = set()
    cached = {}
    blobs = {}

    def blob(desc, *, config=False):
        key = digest(desc["digest"])
        # Configs and ordinary layers have different return values.
        cache_key = (key, config)
        if cache_key not in blobs:
            blobs[cache_key] = registry.blob(desc, config=config)
        return blobs[cache_key]

    def walk(ref, document=None):
        digest(ref)
        if ref in active:
            raise ValueError("Manifest dependency cycle")
        if ref in cached:
            return cached[ref]
        active.add(ref)
        if document is None:
            document, actual = registry.manifest(ref)
            if actual != ref:
                raise ValueError("Manifest digest mismatch: " + ref)
        if document.get("schemaVersion") != 2:
            raise ValueError("Unsupported manifest schema")
        retained.add(ref)
        media_type = document.get("mediaType")
        platforms = []
        if media_type in INDEX_TYPES:
            if not document["manifests"]:
                raise ValueError("Empty manifest index")
            for child in document["manifests"]:
                platforms.extend(walk(digest(child["digest"])))
        elif media_type in MANIFEST_TYPES:
            config = blob(document["config"], config=True)
            for layer in document["layers"]:
                blob(layer)
            if config.get("os") == "linux" and config.get("architecture") in ARCHES:
                platforms.append(config["architecture"])
        else:
            raise ValueError("Unsupported manifest media type")
        if "subject" in document:
            # Attestation subjects are retained, not counted as runnable platforms.
            walk(digest(document["subject"]["digest"]))
        active.remove(ref)
        cached[ref] = platforms
        return platforms

    platforms = walk(latest, root)
    if sorted(platforms) != sorted(ARCHES):
        raise ValueError("latest must contain exactly one linux/amd64 and linux/arm64 image")
    return latest, retained


def version_key(item):
    ident = item["id"]
    tags = item["metadata"]["container"]["tags"]
    if type(ident) is not int or ident <= 0 or not isinstance(tags, list):
        raise ValueError("Invalid package version")
    if any(not isinstance(tag, str) or not tag for tag in tags) or len(set(tags)) != len(tags):
        raise ValueError("Invalid package tags")
    return ident, digest(item["name"]), tuple(sorted(tags))


def deletion_plan(versions, latest, retained):
    by_digest = {}
    ids = set()
    for item in versions:
        ident, name, tags = version_key(item)
        if name in by_digest or ident in ids:
            raise ValueError("Duplicate package version")
        by_digest[name] = item
        ids.add(ident)
        if "latest" in tags and name != latest:
            raise ValueError("API latest differs from registry latest")
        if name in retained and tags != (("latest",) if name == latest else ()):
            raise ValueError("Retained manifest has aliases or missing latest; publish a fresh "
                             "latest-only index before cleanup: " + name)
    if not retained.issubset(by_digest):
        raise ValueError("API inventory is missing retained manifests")
    return [item for item in versions if item["name"] not in retained]


def cleanup(registry, packages, *, apply=False, expected_latest=None):
    latest, retained = manifest_closure(registry, expected_latest)
    candidates = deletion_plan(packages.versions(), latest, retained)

    def unchanged():
        if registry.manifest("latest")[1] != latest:
            raise ValueError("latest changed; refusing further deletion")

    unchanged()
    summary = {"latest": latest, "retain": sorted(retained),
               "delete_ids": [item["id"] for item in candidates], "apply": apply}
    print(json.dumps(summary, sort_keys=True))
    if apply:
        for candidate in candidates:
            if version_key(packages.version(candidate["id"])) != version_key(candidate):
                raise ValueError("Deletion candidate changed; refusing deletion")
            unchanged()  # Immediately before every DELETE, not merely once per run.
            packages.delete(candidate["id"])
        unchanged()
        if deletion_plan(packages.versions(), latest, retained):
            raise ValueError("History remains after cleanup; retry the workflow")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete historical package versions")
    parser.add_argument("--expected-latest", help="require this just-published index digest")
    args = parser.parse_args()
    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository != REPOSITORY:
        raise ValueError("GITHUB_REPOSITORY must be exactly " + REPOSITORY)
    packages = Packages(repository, os.environ.get("GITHUB_TOKEN"))
    packages.verify_target()
    cleanup(PublicRegistry(repository), packages, apply=args.apply, expected_latest=args.expected_latest)


if __name__ == "__main__":
    main()
