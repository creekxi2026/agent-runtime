"""Resolve official tool versions once; skip only if both published arches match."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

ARCHES = {"amd64", "arm64"}
ACCEPT = ",".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


def digest(value):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("Invalid SHA256 image digest")
    return value


class Registry:
    def __init__(self, repository):
        if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        self.repository = repository
        with urllib.request.urlopen(
            "https://ghcr.io/token?scope=repository:" + repository + ":pull", timeout=30
        ) as response:
            self.token = json.load(response)["token"]

    def get(self, path):
        request = urllib.request.Request(
            "https://ghcr.io/v2/" + self.repository + "/" + path,
            headers={"Authorization": "Bearer " + self.token, "Accept": ACCEPT},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
        return json.loads(body), "sha256:" + hashlib.sha256(body).hexdigest()

    def labels(self, descriptor):
        manifest, _ = self.get("manifests/" + digest(descriptor["digest"]))
        config, _ = self.get("blobs/" + digest(manifest["config"]["digest"]))
        return config["config"].get("Labels") or {}


def platforms(index):
    result = {}
    for entry in index.get("manifests", []):
        platform = entry.get("platform", {})
        arch = platform.get("architecture")
        if platform.get("os") == "linux" and arch in ARCHES:
            if arch in result:
                raise ValueError("Duplicate target architecture")
            digest(entry["digest"])
            result[arch] = entry
    if set(result) != ARCHES:
        raise ValueError("Image must contain linux/amd64 and linux/arm64")
    return result


def requires_build(base_digest, revision, published_labels, dependency_digest=None):
    digest(base_digest)
    if dependency_digest is not None:
        digest(dependency_digest)
    return set(published_labels) != ARCHES or any(
        labels.get("org.opencontainers.image.base.digest") != base_digest
        or labels.get("org.opencontainers.image.revision") != revision
        or (dependency_digest is not None and labels.get("io.creek.runtime.dependencies") != dependency_digest)
        for labels in published_labels.values()
    )


def main():
    revision = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid revision")
    lock_path = Path("runtime-deps.json")
    subprocess.run([sys.executable, "scripts/resolve_tools.py", "--output", str(lock_path)], check=True)
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    base_digest = digest(lock["base_digest"])
    if not lock["base_image"].endswith("@" + base_digest):
        raise ValueError("Base image and digest disagree")
    dependency_digest = "sha256:" + hashlib.sha256(lock_bytes).hexdigest()
    current = Registry(os.environ["GITHUB_REPOSITORY"].lower())
    try:
        published, _ = current.get("manifests/latest")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise  # Never hide authentication, rate-limit or network failures.
        published_labels = {}
    else:
        published_labels = {
            arch: current.labels(entry) for arch, entry in platforms(published).items()
        }
    version = Path("VERSION").read_text().strip()
    tag = f"{version}-build-{os.environ.get('GITHUB_RUN_ID', 'local')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}", tag):
        raise ValueError("Invalid build tag")
    outputs = {
        "base_digest": base_digest,
        "base_image": lock["base_image"],
        "dependency_digest": dependency_digest,
        "build_tag": tag,
        "changed": str(requires_build(base_digest, revision, published_labels, dependency_digest)).lower(),
    }
    print(json.dumps(outputs, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for key, value in outputs.items():
                output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
