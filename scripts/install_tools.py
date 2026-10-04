#!/usr/bin/env python3
"""Build-time installation from the official lock or isolated component shards.

Run as root in a fresh Debian image. Never resolve latest in this installer.
The optional --verify-only mode performs executable/package version checks.
Use --component core/codex/multica with the corresponding split input; the
default all mode retains the original full-manifest installation interface.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request

PREFIX = Path("/opt/agent-tools")
NODE = Path("/opt/node")
GO = Path("/opt/go")
CODEX = Path("/opt/codex")
MULTICA = Path("/opt/multica/bin")


def validate_component(manifest, component):
    """Fail closed if a stage receives another component's lock or extra tools."""
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    if component == "all":
        if "component" in manifest:
            raise ValueError("Full installation requires the full manifest")
        return
    schemas = {
        "core": ({"node", "npm", "go", "gh", "uv", "lark_cli", "pnpm", "playwright_cli", "playwright", "miniprogram_ci"},
                 {"npm", "@larksuite/cli", "pnpm", "@playwright/cli", "playwright", "miniprogram-ci"},
                 {"node", "go", "gh", "uv"}),
        "codex": ({"codex"}, {"@openai/codex"}, set()),
        "multica": ({"multica"}, set(), {"multica"}),
    }
    if component not in schemas or manifest.get("component") != component:
        raise ValueError("Incorrect component manifest")
    fields = {"schema_version", "component", "versions", "npm", "artifacts"}
    if component == "core":
        fields.update({"base_image", "base_digest", "base_platforms"})
        fields.update(set(manifest) & {"official_skills", "chromium"})
    if set(manifest) != fields:
        raise ValueError("Unexpected component manifest fields")
    for field, expected in zip(("versions", "npm", "artifacts"), schemas[component]):
        if set(manifest[field]) != expected:
            raise ValueError("Unexpected component " + field)
    if component == "codex" and manifest["versions"]["codex"] != manifest["npm"]["@openai/codex"]["version"]:
        raise ValueError("Conflicting Codex versions")
    for platforms in manifest["artifacts"].values():
        if set(platforms) != {"amd64", "arm64"}:
            raise ValueError("Expected both supported architectures")


def verify_file(path, algorithm, expected):
    actual = hashlib.new(algorithm)
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            actual.update(chunk)
    if not hmac.compare_digest(actual.hexdigest(), expected):
        raise ValueError("Checksum mismatch: " + str(path))


def verify_integrity(path, integrity):
    if not integrity.startswith("sha512-"):
        raise ValueError("Expected npm SHA512 integrity")
    raw = base64.b64decode(integrity[7:], validate=True)
    if len(raw) != 64:
        raise ValueError("Invalid npm integrity length")
    verify_file(path, "sha512", raw.hex())


def download(artifact, destination):
    parsed = urllib.parse.urlsplit(artifact["url"])
    if parsed.scheme != "https" or parsed.netloc not in {
        "nodejs.org", "go.dev", "github.com", "registry.npmjs.org", "codeload.github.com"
    } or parsed.query or parsed.fragment:
        raise ValueError("Unexpected artifact URL")
    # No credentials are accepted by the build-time installer. Official release
    # URLs may redirect to a CDN; only the fixed source URL is recorded in JSON.
    request = urllib.request.Request(artifact["url"], headers={"User-Agent": "agent-runtime-installer"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)
    if "sha256" in artifact:
        expected = artifact["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid SHA256")
        verify_file(destination, "sha256", expected)
    else:
        verify_integrity(destination, artifact["integrity"])
    return destination


def extract(archive, destination):
    """Only files, directories and in-tree links; never tar ownership/modes.

    Implement explicitly so the same offline tests work on Python 3.9 hosts
    without tarfile.data_filter as well as the Debian image's current Python.
    """
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()

    def inside(path):
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("Archive path escapes destination")
        return path

    with tarfile.open(archive, "r:*") as source:
        for member in source:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError("Unsafe archive member")
            target = inside(root / member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise ValueError("Duplicate archive member")
            if member.isfile():
                data = source.extractfile(member)
                if data is None:
                    raise ValueError("Unreadable archive member")
                with data, target.open("xb") as out:
                    shutil.copyfileobj(data, out)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            elif member.issym():
                if PurePosixPath(member.linkname).is_absolute():
                    raise ValueError("Absolute archive symlink")
                inside(target.parent / member.linkname)
                target.symlink_to(member.linkname)
            elif member.islnk():
                if PurePosixPath(member.linkname).is_absolute():
                    raise ValueError("Absolute archive hardlink")
                link = inside(root / member.linkname)
                if not link.is_file():
                    raise ValueError("Unresolved archive hardlink")
                os.link(link, target)
            else:
                raise ValueError("Unsupported archive member")


def npm_plan(manifest):
    # Pin direct tools, but preserve upstream CLI internal dependency contracts.
    # Forcing a CLI's alpha/internal Playwright onto a different stable API can
    # break real operations even when --version and --help still pass.
    return {"name": "agent-runtime-tools", "version": "1.0.0", "private": True,
            "dependencies": {name: item["version"] for name, item in sorted(manifest["npm"].items()) if name != "npm"},
            # npm 12 requires explicit approvals. Native CLI/bootstrap scripts
            # are permitted only for these packages, not globally for users.
            "allowScripts": {
                **{name + "@" + item["version"]: True for name, item in manifest["npm"].items()
                   if name in {"pnpm", "@larksuite/cli"}},
                "@swc/core": True, "@parcel/watcher": True,
                "core-js": False, "less": False, "protobufjs": False,
            }}


def check_version(command, expected):
    output = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=60)
    if not re.search(r"(?<![0-9.])" + re.escape(expected) + r"(?![0-9.\-+])", output):
        raise ValueError("Unexpected version from " + command[0] + ": " + output.strip())
    print(command[0] + ": " + expected, flush=True)


def install_archive(tool, artifact, temporary, binary_dir=Path("/usr/local/bin")):
    archive = download(artifact, temporary / (tool + ".archive"))
    destination = temporary / tool
    extract(archive, destination)
    if tool in ("node", "go"):
        children = list(destination.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            raise ValueError("Expected a single toolchain archive root")
        target = NODE if tool == "node" else GO
        if target.exists():
            raise ValueError("Refusing to install a second " + tool + " toolchain")
        shutil.move(str(children[0]), str(target))
    else:
        for binary in (("uv", "uvx") if tool == "uv" else (tool,)):
            candidates = [p for p in destination.rglob(binary) if p.is_file()]
            if len(candidates) != 1:
                raise ValueError("Missing or ambiguous binary: " + binary)
            binary_dir.mkdir(parents=True, exist_ok=True)
            target = binary_dir / binary
            shutil.copyfile(candidates[0], target)
            target.chmod(0o755)


def install(manifest, arch, component="all"):
    validate_component(manifest, component)
    if os.geteuid() != 0:
        raise ValueError("Image installation requires root")
    if component in ("codex", "multica"):
        with tempfile.TemporaryDirectory(prefix="agent-tools-") as temporary:
            tmp = Path(temporary)
            if component == "codex":
                install_npm(manifest, tmp, CODEX)
            else:
                install_archive("multica", manifest["artifacts"]["multica"][arch], tmp, MULTICA)
        verify(manifest, component)
        return
    for root in (Path("/opt"), Path("/usr/local/bin"), PREFIX):
        root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agent-tools-") as temporary:
        tmp = Path(temporary)
        tools = ("node", "go", "gh", "uv", "multica") if component == "all" else ("node", "go", "gh", "uv")
        for tool in tools:
            install_archive(tool, manifest["artifacts"][tool][arch], tmp)
        # Replace Node's bundled npm in this same image layer. Exactly one npm
        # installation remains, at the normal Node-distribution symlink target.
        archive = download(manifest["npm"]["npm"], tmp / "npm.tgz")
        extract(archive, tmp / "npm-package")
        npm_root = NODE / "lib/node_modules/npm"
        shutil.rmtree(npm_root)
        shutil.move(str(tmp / "npm-package/package"), str(npm_root))
        for name in ("npm", "npx"):
            link = NODE / "bin" / name
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to("../lib/node_modules/npm/bin/" + name + "-cli.js")

        install_npm(manifest, tmp, PREFIX)
    verify(manifest, component)


def install_npm(manifest, tmp, prefix):
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / "package.json").write_text(json.dumps(npm_plan(manifest), sort_keys=True, indent=2) + "\n")
    npm = str(NODE / "bin/npm")
    env = {**os.environ, "PATH": str(NODE / "bin") + ":" + os.environ.get("PATH", ""),
           "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "CI": "1", "NO_UPDATE_NOTIFIER": "1",
           "npm_config_cache": str(tmp / "npm-cache"), "npm_config_registry": "https://registry.npmjs.org"}
    # Prime npm's cache from verified direct tarballs; preserve upstream
    # transitive contracts and record the resolved tree in package-lock.json.
    for index, (name, artifact) in enumerate(sorted(manifest["npm"].items())):
        if name == "npm":
            continue
        archive = download(artifact, tmp / ("npm-" + str(index) + ".tgz"))
        subprocess.run([npm, "cache", "add", str(archive)], env=env, check=True)
    subprocess.run([npm, "install", "--prefix", str(prefix), "--no-audit", "--no-fund", "--prefer-offline"], env=env, check=True)
    (prefix / "bin").mkdir(exist_ok=True)
    for source in sorted((prefix / "node_modules/.bin").iterdir()):
        if source.is_symlink() and source.resolve().is_file():
            (prefix / "bin" / source.name).symlink_to(os.path.relpath(source.resolve(), (prefix / "bin").resolve()))


def verify(manifest, component="all"):
    validate_component(manifest, component)
    versions = manifest["versions"]
    commands = {"node": [str(NODE / "bin/node"), "--version"],
                "npm": [str(NODE / "bin/npm"), "--version"],
                "go": [str(GO / "bin/go"), "version"],
                "gh": ["/usr/local/bin/gh", "--version"],
                "uv": ["/usr/local/bin/uv", "--version"],
                "multica": [str(MULTICA / "multica") if component == "multica" else "/usr/local/bin/multica", "--version"],
                "codex": [str((CODEX if component == "codex" else PREFIX) / "bin/codex"), "--version"],
                "lark_cli": [str(PREFIX / "bin/lark-cli"), "--version"],
                "pnpm": [str(PREFIX / "bin/pnpm"), "--version"],
                "playwright_cli": [str(PREFIX / "bin/playwright-cli"), "--version"]}
    for name, command in commands.items():
        if component == "all" or name in versions:
            check_version(command, versions[name])
    if component in ("all", "core"):
        subprocess.run([str(PREFIX / "bin/playwright-cli"), "--help"], check=True, stdout=subprocess.DEVNULL, timeout=60)
    for name, artifact in manifest["npm"].items():
        prefix = CODEX if component == "codex" else PREFIX
        # A full-manifest verification also supports the assembled split image.
        if component == "all" and name == "@openai/codex" and CODEX.exists():
            prefix = CODEX
        root = NODE / "lib/node_modules/npm" if name == "npm" else prefix / "node_modules" / name
        actual = json.loads((root / "package.json").read_text())["version"]
        if actual != artifact["version"]:
            raise ValueError("Incorrect installed npm package: " + name)
    # Globally exposed Playwright is usable as a JS client, without browsers.
    if component in ("all", "core"):
        subprocess.run([str(NODE / "bin/node"), "-e", "const p=require('/opt/agent-tools/node_modules/playwright'); if(typeof p.chromium.connect !== 'function') process.exit(1)"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--base-image", help="Assert Docker FROM matches the shared manifest")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--component", choices=("all", "core", "codex", "multica"), default="all")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    validate_component(manifest, args.component)
    if args.base_image and args.base_image != manifest["base_image"]:
        raise ValueError("Docker base does not match dependency manifest; pass --build-arg BASE_IMAGE")
    os.environ.update({"PATH": "/opt/agent-tools/bin:/opt/node/bin:/opt/go/bin:/usr/local/bin:/usr/bin:/bin",
                       "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "CI": "1", "NO_UPDATE_NOTIFIER": "1"})
    if args.verify_only:
        verify(manifest, args.component)
    else:
        install(manifest, args.arch, args.component)


if __name__ == "__main__":
    main()
