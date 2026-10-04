#!/usr/bin/env python3
"""Resolve official stable tool releases ONCE for both Linux architectures.

No timestamps, credentials or expiring download URLs enter the manifest. Debian
Chromium packages are resolved from signed apt metadata before the build and
locked per architecture. Other apt packages are not independently tracked.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

ARCHES = ("amd64", "arm64")
NPM_PACKAGES = {
    "codex": "@openai/codex", "lark_cli": "@larksuite/cli",
    "miniprogram_ci": "miniprogram-ci", "playwright_cli": "@playwright/cli",
    "playwright": "playwright", "pnpm": "pnpm", "npm": "npm",
}
ACCEPT = ",".join(("application/vnd.oci.image.index.v1+json",
                   "application/vnd.docker.distribution.manifest.list.v2+json",
                   "application/vnd.oci.image.manifest.v1+json",
                   "application/vnd.docker.distribution.manifest.v2+json"))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """urllib otherwise forwards Authorization across redirect origins."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if result is not None and (old.scheme, old.netloc) != (new.scheme, new.netloc):
            result.remove_header("Authorization")
        if new.scheme != "https":
            raise ValueError("Refusing non-HTTPS redirect")
        return result


def get_bytes(url, headers=None):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("HTTPS is required")
    req = urllib.request.Request(url, headers={"User-Agent": "agent-runtime-resolver", **(headers or {})})
    with urllib.request.build_opener(SafeRedirect()).open(req, timeout=60) as response:
        return response.read()


def get_json(url, headers=None):
    return json.loads(get_bytes(url, headers))


def github_headers(url):
    parsed = urllib.parse.urlsplit(url)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token and parsed.scheme == "https" and parsed.netloc == "api.github.com":
        headers["Authorization"] = "Bearer " + token
    return headers


def github_json(path):
    url = "https://api.github.com/" + path.lstrip("/")
    # gh supports an already-authenticated local workstation without exposing its
    # token. CI uses GH_TOKEN/GITHUB_TOKEN directly, only at api.github.com.
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")) and shutil.which("gh"):
        result = subprocess.run(["gh", "api", "--hostname", "github.com", path],
                                capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return json.loads(result.stdout)
    return get_json(url, github_headers(url))


def stable_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value):
        raise ValueError("Expected a formal stable semantic version")
    return value.removeprefix("v")


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("Invalid SHA256 digest")
    return value


def checksum(text, filename):
    matches = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].removeprefix("*") == filename:
            matches.append(digest("sha256:" + fields[0]).removeprefix("sha256:"))
    if len(matches) != 1:
        raise ValueError("Expected exactly one checksum for " + filename)
    return matches[0]


def platforms(index):
    result = {}
    for entry in index.get("manifests", []):
        platform = entry.get("platform", {})
        arch = platform.get("architecture")
        if platform.get("os") == "linux" and arch in ARCHES:
            if arch in result:
                raise ValueError("Duplicate target architecture")
            result[arch] = digest(entry["digest"])
    if set(result) != set(ARCHES):
        raise ValueError("Image must include linux/amd64 and linux/arm64")
    return result


def debian_base():
    auth = get_json("https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/debian:pull")
    headers = {"Accept": ACCEPT, "Authorization": "Bearer " + auth["token"]}
    body = get_bytes("https://registry-1.docker.io/v2/library/debian/manifests/stable-slim", headers)
    base_digest = "sha256:" + hashlib.sha256(body).hexdigest()
    return {"base_image": "debian:stable-slim@" + base_digest,
            "base_digest": base_digest, "base_platforms": platforms(json.loads(body))}


def release(repository):
    data = github_json("repos/" + repository + "/releases/latest")
    if data.get("draft") or data.get("prerelease"):
        raise ValueError("Latest release is not stable: " + repository)
    stable_version(data["tag_name"])
    return data


def release_artifact(repository, data, filename):
    matches = [a for a in data["assets"] if a["name"] == filename]
    if len(matches) != 1:
        raise ValueError("Missing or duplicate official asset: " + filename)
    asset = matches[0]
    url = "https://github.com/" + repository + "/releases/download/" + data["tag_name"] + "/" + filename
    if asset["browser_download_url"] != url:
        raise ValueError("Unexpected release download URL")
    return {"url": url, "sha256": digest(asset.get("digest")).removeprefix("sha256:")}


def npm_package(name):
    data = get_json("https://registry.npmjs.org/" + urllib.parse.quote(name, safe="") + "/latest")
    version = stable_version(data["version"])
    dist = data["dist"]
    url = urllib.parse.urlsplit(dist["tarball"])
    if data.get("name") != name or url.scheme != "https" or url.netloc != "registry.npmjs.org" or url.query or url.fragment:
        raise ValueError("Unexpected npm package identity or tarball URL")
    integrity = dist["integrity"]
    if not integrity.startswith("sha512-") or len(base64.b64decode(integrity[7:], validate=True)) != 64:
        raise ValueError("npm tarball must have SHA512 integrity")
    return {"version": version, "url": dist["tarball"], "integrity": integrity}


def resolve():
    result = {"schema_version": 1, **debian_base(), "versions": {}, "artifacts": {}, "npm": {}}
    versions, artifacts = result["versions"], result["artifacts"]
    # Current is stable, not necessarily LTS. Reject RC/nightly lines entirely.
    nodes = get_json("https://nodejs.org/dist/index.json")
    nodes = [n for n in nodes if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", n["version"])]
    node = max(nodes, key=lambda n: tuple(map(int, stable_version(n["version"]).split("."))))
    versions["node"], versions["npm"] = stable_version(node["version"]), stable_version(node["npm"])
    node_root = "https://nodejs.org/dist/" + node["version"] + "/"
    sums = get_bytes(node_root + "SHASUMS256.txt").decode()
    artifacts["node"] = {}
    for arch, upstream in (("amd64", "x64"), ("arm64", "arm64")):
        filename = "node-" + node["version"] + "-linux-" + upstream + ".tar.xz"
        artifacts["node"][arch] = {"url": node_root + filename, "sha256": checksum(sums, filename)}

    go_releases = get_json("https://go.dev/dl/?mode=json")
    go = max((g for g in go_releases if g.get("stable")),
             key=lambda g: tuple(map(int, stable_version(g["version"].removeprefix("go")).split("."))))
    versions["go"] = stable_version(go["version"].removeprefix("go"))
    artifacts["go"] = {}
    for arch in ARCHES:
        files = [f for f in go["files"] if f["os"] == "linux" and f["arch"] == arch and f["kind"] == "archive"]
        if len(files) != 1:
            raise ValueError("Go release lacks an unambiguous Linux archive")
        file = files[0]
        expected = go["version"] + ".linux-" + arch + ".tar.gz"
        if file["filename"] != expected:
            raise ValueError("Unexpected Go filename")
        artifacts["go"][arch] = {"url": "https://go.dev/dl/" + expected,
                                  "sha256": digest("sha256:" + file["sha256"])[7:]}

    for tool, repo in (("gh", "cli/cli"), ("uv", "astral-sh/uv"), ("multica", "multica-ai/multica")):
        data = release(repo)
        version = versions[tool] = stable_version(data["tag_name"])
        artifacts[tool] = {}
        for arch in ARCHES:
            upstream = {"amd64": "x86_64", "arm64": "aarch64"}[arch]
            filename = {"gh": f"gh_{version}_linux_{arch}.tar.gz",
                        "uv": f"uv-{upstream}-unknown-linux-gnu.tar.gz",
                        "multica": f"multica-cli-{version}-linux-{arch}.tar.gz"}[tool]
            artifacts[tool][arch] = release_artifact(repo, data, filename)
    for key, name in sorted(NPM_PACKAGES.items()):
        package = npm_package(name)
        result["npm"][name] = package
        versions[key] = package["version"]
    from official_inputs import chromium_inputs, lark_skills_inputs
    result['official_skills'] = {'lark': lark_skills_inputs(github_json, get_bytes)}
    result['chromium'] = chromium_inputs(result['base_image'])
    return result


def serialize(manifest):
    return json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("runtime-deps.json"))
    args = parser.parse_args()
    manifest = resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialize(manifest), encoding="utf-8")
    print(serialize({"base_image": manifest["base_image"], "versions": manifest["versions"]}), end="")


if __name__ == "__main__":
    main()
