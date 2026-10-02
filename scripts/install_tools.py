#!/usr/bin/env python3
"""Build-time installation only, from the shared official dependency manifest.

Run as root in a fresh Debian image. Never resolve latest in this installer.
The optional --verify-only mode performs executable/package version checks.
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
        "nodejs.org", "go.dev", "github.com", "registry.npmjs.org"
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
            "dependencies": {name: item["version"] for name, item in sorted(manifest["npm"].items()) if name != "npm"}}


def check_version(command, expected):
    output = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=60)
    if not re.search(r"(?<![0-9.])" + re.escape(expected) + r"(?![0-9.\-+])", output):
        raise ValueError("Unexpected version from " + command[0] + ": " + output.strip())
    print(command[0] + ": " + expected, flush=True)


def install_archive(tool, artifact, temporary):
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
            target = Path("/usr/local/bin") / binary
            shutil.copyfile(candidates[0], target)
            target.chmod(0o755)


def install(manifest, arch):
    if os.geteuid() != 0:
        raise ValueError("Image installation requires root")
    for root in (Path("/opt"), Path("/usr/local/bin"), PREFIX):
        root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agent-tools-") as temporary:
        tmp = Path(temporary)
        for tool in ("node", "go", "gh", "uv", "multica"):
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

        (PREFIX / "package.json").write_text(json.dumps(npm_plan(manifest), sort_keys=True, indent=2) + "\n")
        npm = str(NODE / "bin/npm")
        env = {**os.environ, "PATH": str(NODE / "bin") + ":" + os.environ.get("PATH", ""),
               "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "CI": "1", "NO_UPDATE_NOTIFIER": "1",
               "npm_config_cache": str(tmp / "npm-cache"), "npm_config_registry": "https://registry.npmjs.org"}
        # Prime npm's content-addressed cache from verified top-level tarballs.
        # Dependency ranges within official npm packages are resolved by npm;
        # package-lock.json records that build's complete dependency tree.
        for index, (name, artifact) in enumerate(sorted(manifest["npm"].items())):
            if name == "npm":
                continue
            archive = download(artifact, tmp / ("npm-" + str(index) + ".tgz"))
            subprocess.run([npm, "cache", "add", str(archive)], env=env, check=True)
        subprocess.run([npm, "install", "--prefix", str(PREFIX), "--no-audit", "--no-fund", "--prefer-offline"], env=env, check=True)
        (PREFIX / "bin").mkdir(exist_ok=True)
        # Expose only actual installed package binaries, without another global
        # npm copy or an extra toolchain under the persistent user's HOME.
        for source in sorted((PREFIX / "node_modules/.bin").iterdir()):
            if source.is_symlink() and source.resolve().is_file():
                (PREFIX / "bin" / source.name).symlink_to(os.path.relpath(source.resolve(), PREFIX / "bin"))
    verify(manifest)


def verify(manifest):
    versions = manifest["versions"]
    commands = {"node": [str(NODE / "bin/node"), "--version"],
                "npm": [str(NODE / "bin/npm"), "--version"],
                "go": [str(GO / "bin/go"), "version"],
                "gh": ["/usr/local/bin/gh", "--version"],
                "uv": ["/usr/local/bin/uv", "--version"],
                "multica": ["/usr/local/bin/multica", "--version"],
                "codex": [str(PREFIX / "bin/codex"), "--version"],
                "lark_cli": [str(PREFIX / "bin/lark-cli"), "--version"],
                "pnpm": [str(PREFIX / "bin/pnpm"), "--version"],
                "playwright_cli": [str(PREFIX / "bin/playwright-cli"), "--version"]}
    for name, command in commands.items():
        check_version(command, versions[name])
    subprocess.run([str(PREFIX / "bin/playwright-cli"), "--help"], check=True, stdout=subprocess.DEVNULL, timeout=60)
    for name, artifact in manifest["npm"].items():
        root = NODE / "lib/node_modules/npm" if name == "npm" else PREFIX / "node_modules" / name
        actual = json.loads((root / "package.json").read_text())["version"]
        if actual != artifact["version"]:
            raise ValueError("Incorrect installed npm package: " + name)
    # Globally exposed Playwright is usable as a JS client, without browsers.
    subprocess.run([str(NODE / "bin/node"), "-e", "const p=require('/opt/agent-tools/node_modules/playwright'); if(typeof p.chromium.connect !== 'function') process.exit(1)"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--base-image", help="Assert Docker FROM matches the shared manifest")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported manifest schema")
    if args.base_image and args.base_image != manifest["base_image"]:
        raise ValueError("Docker base does not match dependency manifest; pass --build-arg BASE_IMAGE")
    os.environ.update({"PATH": "/opt/agent-tools/bin:/opt/node/bin:/opt/go/bin:/usr/local/bin:/usr/bin:/bin",
                       "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "CI": "1", "NO_UPDATE_NOTIFIER": "1"})
    if args.verify_only:
        verify(manifest)
    else:
        install(manifest, args.arch)


if __name__ == "__main__":
    main()
