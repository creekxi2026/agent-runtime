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


def platforms(index, *, require_complete=True):
    result = {}
    for entry in index.get("manifests", []):
        platform = entry.get("platform", {})
        arch = platform.get("architecture")
        if platform.get("os") == "linux" and arch in ARCHES:
            if arch in result:
                raise ValueError("Duplicate target architecture")
            digest(entry["digest"])
            result[arch] = entry
    if require_complete and set(result) != ARCHES:
        raise ValueError("Image must contain linux/amd64 and linux/arm64")
    return result


def publication_labels(registry, image):
    """Incomplete known images rebuild; malformed data and fetch errors still fail."""
    if not isinstance(image, dict) or image.get("schemaVersion") != 2:
        raise ValueError("Invalid published image schema")
    media_type = image.get("mediaType")
    if media_type in ACCEPT.split(",")[:2]:
        if not isinstance(image.get("manifests"), list):
            raise ValueError("Invalid published image manifests")
        for entry in image["manifests"]:
            validate_descriptor(entry)
            platform = entry.get("platform", {})
            if not isinstance(platform, dict) or ("platform" in entry and any(
                not isinstance(platform.get(key), str) or not platform[key]
                for key in ("os", "architecture")
            )):
                raise ValueError("Invalid published image platform")
        return {arch: registry.labels(entry)
                for arch, entry in platforms(image, require_complete=False).items()}
    if media_type in ACCEPT.split(",")[2:]:
        validate_descriptor(image["config"])
        if image["config"]["mediaType"] not in (
            "application/vnd.oci.image.config.v1+json",
            "application/vnd.docker.container.image.v1+json",
        ) or not isinstance(image.get("layers"), list):
            raise ValueError("Invalid published image manifest")
        for layer in image["layers"]:
            validate_descriptor(layer)
        config, _ = registry.get("blobs/" + digest(image["config"]["digest"]))
        if (not isinstance(config, dict) or not isinstance(config.get("config"), dict)
                or not isinstance(config.get("os"), str) or not config["os"]
                or not isinstance(config.get("architecture"), str) or not config["architecture"]):
            raise ValueError("Invalid published image config")
        labels = config["config"].get("Labels")
        if labels is None:
            labels = {}
        if not isinstance(labels, dict):
            raise ValueError("Invalid published image labels")
        return {config["architecture"]: labels} if config["os"] == "linux" else {}
    raise ValueError("Invalid published image media type")


def validate_descriptor(entry):
    if not isinstance(entry, dict):
        raise ValueError("Invalid image descriptor")
    digest(entry["digest"])
    if (not isinstance(entry.get("mediaType"), str) or not entry["mediaType"]
            or type(entry.get("size")) is not int or entry["size"] < 0):
        raise ValueError("Invalid image descriptor media type or size")


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
        published_labels = publication_labels(current, published)
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
