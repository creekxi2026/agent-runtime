#!/usr/bin/env python3
"""Relocate an upstream tool HOME in a disposable build stage, never at startup.

Only bounded UTF-8 text is rewritten. Binary/pyc debug strings are NOT claimed
relocatable. Unsupported runtime text/links fail the build; no HOME aliases are
created. Failure after rename is not rolled back: discard the build stage.
"""
import argparse
import codecs
import hashlib
import os
from pathlib import Path
import re
import stat


TEXT_LIMIT = 4 * 1024 * 1024


def _walk(root):
    def fail(error):
        raise error
    yield root
    for base, directories, files in os.walk(root, followlinks=False, onerror=fail):
        for name in directories + files:
            yield Path(base) / name


def _map_absolute(value, source, destination):
    if value == source or value.startswith(source + "/"):
        normalized = os.path.normpath(value)
        if normalized != source and not normalized.startswith(source + "/"):
            raise ValueError("unsupported escaping absolute link/path: " + value)
        return destination + value[len(source):]
    return value


def _text(path, pattern, source, destination, limit, validate=False):
    if path.suffix == ".pyc":  # CPython debug/co_filename paths are not runtime links.
        return
    known = path.suffix in {".pth", ".cfg", ".toml", ".sh"} or path.name.startswith("activate")
    with path.open("rb") as stream:
        data = stream.read(8192)
        known = known or data.startswith(b"#!")
        try:
            if re.search(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]", data):
                raise UnicodeError("binary control bytes")
            codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
        except UnicodeError:
            if known:
                raise ValueError("unsupported runtime text encoding/binary: " + str(path))
            return
        if path.stat().st_size > limit:
            # Stream text-looking large files, but never load a Go/Chromium binary.
            needle, carry = source.encode(), b""
            while data:
                if needle in carry + data:
                    raise ValueError("text exceeds rewrite size limit with old prefix: " + str(path))
                carry = (carry + data)[-len(needle):]
                data = stream.read(8192)
            return
        data += stream.read(limit + 1 - len(data))
    try:
        text = data.decode("utf-8")
        if re.search(r"[\x00-\x08\x0b\x0e-\x1f\x7f]", text):
            raise UnicodeError("binary control bytes")
    except UnicodeError:
        if known:
            raise ValueError("unsupported runtime text encoding/binary: " + str(path))
        return
    updated = pattern.sub(lambda match: (match.group(1) or "") + destination, text)
    if updated != text:
        if validate:
            raise ValueError("residual old runtime prefix: " + str(path))
        path.write_bytes(updated.encode("utf-8"))


def _playwright(directory, source, destination):
    for record in list(directory.iterdir()):
        if record.is_symlink() or not record.is_file() or record.stat().st_size > 8192:
            raise ValueError("unsupported Playwright registration: " + str(record))
        package = record.read_text(encoding="utf-8").strip()
        if not package.startswith("/") or "\n" in package or "\r" in package:
            raise ValueError("invalid Playwright registration: " + str(record))
        if record.name != hashlib.sha1(package.encode()).hexdigest():
            raise ValueError("invalid Playwright registration SHA1: " + str(record))
        updated = _map_absolute(package, source, destination)
        target = directory / hashlib.sha1(updated.encode()).hexdigest()
        if updated != package:
            if os.path.lexists(target):
                raise ValueError("Playwright registration collision: " + str(target))
            record.write_text(updated, encoding="utf-8")
            record.rename(target)


def relocate(source="/home/agent", destination="/opt/agent-upstream", *,
             home_uid=1000, home_gid=1000, tool_uid=0, tool_gid=0,
             skip_chown=False, max_text_bytes=TEXT_LIMIT):
    """Move entire source; rewrite, validate, root-own, then recreate empty HOME.

    Owner overrides/skip_chown are for isolated non-root tests. Production uses
    defaults. Both roots must be absolute and disjoint, destination must not exist.
    """
    source, destination = Path(source), Path(destination)
    if not source.is_absolute() or not destination.is_absolute():
        raise ValueError("source and destination must be absolute")
    old, new = str(source), str(destination)
    a, b = source.resolve(), destination.resolve()
    if a == b or a in b.parents or b in a.parents:
        raise ValueError("source and destination must not overlap")
    if source.is_symlink() or not source.is_dir():
        raise ValueError("source must be a real directory")
    if os.path.lexists(destination):
        raise FileExistsError("destination already exists: " + new)
    if min(home_uid, home_gid, tool_uid, tool_gid) < 0 or max_text_bytes < 1:
        raise ValueError("owners must be nonnegative and text limit positive")
    # Include file:// URLs; exclude /other/home/agent and /home/agent-backup.
    pattern = re.compile(r"(?:(?<![\w./-])|(?<=:-))(file://)?" + re.escape(old)
                         + r"(?=$|[/\s\"'`:;,\)\]}])")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.rename(source, destination)  # Fail on EXDEV rather than losing hardlink structure.
    for path in _walk(destination):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            updated = _map_absolute(target, old, new)
            if not os.path.isabs(target):
                before = os.path.normpath(source / path.relative_to(destination).parent / target)
                after = os.path.normpath(path.parent / target)
                if before != old and not before.startswith(old + "/") and before != after:
                    raise ValueError("unsupported relative link escapes relocated tree: " + str(path))
            if updated != target:
                path.unlink()
                path.symlink_to(updated)
        elif stat.S_ISREG(mode):
            _text(path, pattern, old, new, max_text_bytes)
        elif stat.S_ISDIR(mode):
            if path.name == ".links" and path.parent.name == "ms-playwright":
                _playwright(path, old, new)
        else:
            raise ValueError("unsupported special file: " + str(path))
    # Verify all determinable runtime references; never follow external symlinks.
    for path in _walk(destination):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            if _map_absolute(target, old, new) != target:
                raise ValueError("residual old symlink: " + str(path))
            continue
        if stat.S_ISREG(mode):
            _text(path, pattern, old, new, max_text_bytes, validate=True)
        if not skip_chown:
            os.chown(path, tool_uid, tool_gid, follow_symlinks=False)
        # root-owned tools remain readable/traversable even when HOME was 0700.
        executable = stat.S_ISDIR(mode) or bool(mode & 0o111)
        path.chmod((mode | 0o444 | (0o111 if executable else 0)) & 0o755)
    source.mkdir(mode=0o755)
    source.chmod(0o755)
    if not skip_chown:
        os.chown(source, home_uid, home_gid, follow_symlinks=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="/home/agent")
    parser.add_argument("--destination", default="/opt/agent-upstream")
    for name, default in [("home-uid", 1000), ("home-gid", 1000), ("tool-uid", 0), ("tool-gid", 0)]:
        parser.add_argument("--" + name, type=int, default=default)
    parser.add_argument("--skip-chown", action="store_true", help="non-root fixture tests only")
    parser.add_argument("--max-text-bytes", type=int, default=TEXT_LIMIT)
    try:
        relocate(**vars(parser.parse_args()))
    except (OSError, ValueError) as exc:
        parser.exit(1, "relocation failed: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
