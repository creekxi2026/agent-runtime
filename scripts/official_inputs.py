#!/usr/bin/env python3
"""Official skills and signed Debian Chromium inputs; network failures are fatal.

Docker supplies Debian's archive keyring/apt verifier. Cross-architecture package
metadata is resolved without executing foreign binaries or requiring QEMU.
"""
import hashlib
import re
import subprocess


def package_record(text, name, arch):
    fields = dict(line.split(': ', 1) for line in text.splitlines() if ': ' in line and not line.startswith(' '))
    if fields.get('Package') != name or fields.get('Architecture') not in (arch, 'all'):
        raise ValueError('Unexpected Debian package identity')
    if not re.fullmatch(r'[0-9a-f]{64}', fields.get('SHA256', '')):
        raise ValueError('Missing Debian package checksum')
    if not re.fullmatch(r'[0-9A-Za-z.+:~_-]+', fields.get('Version', '')):
        raise ValueError('Invalid Debian version')
    return {'version': fields['Version'], 'sha256': fields['SHA256']}


def chromium_inputs(base_image):
    result = {}
    for arch in ('amd64', 'arm64'):
        # Native resolver architecture is independent from requested apt metadata.
        output = subprocess.check_output([
            'docker', 'run', '--rm', '--user', '0:0', '--entrypoint', 'sh', base_image,
            '-ec', 'apt-get -o APT::Architecture="$1" -o APT::Update::Error-Mode=any update >&2; '
            'for p in chromium chromium-common chromium-sandbox; do '
            'apt-cache -o APT::Architecture="$1" --no-all-versions show "$p"; '
            'printf "\\n---PACKAGE---\\n"; done', 'resolve', arch,
        ], text=True, timeout=180)
        records = output.split('\n---PACKAGE---\n')
        result[arch] = {name: package_record(records[i].strip(), name, arch)
                        for i, name in enumerate(('chromium', 'chromium-common', 'chromium-sandbox'))}
    return result


def lark_skills_inputs(github_json, get_bytes):
    # Resolve the last commit that changed skills, not unrelated CLI changes.
    commit = github_json('repos/larksuite/cli/commits?path=skills&per_page=1')[0]['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Invalid official skills revision')
    tree = github_json('repos/larksuite/cli/git/trees/' + commit)
    matches = [e for e in tree['tree'] if e['path'] == 'skills' and e['type'] == 'tree']
    if len(matches) != 1 or tree.get('truncated'):
        raise ValueError('Missing complete official skills tree')
    url = 'https://codeload.github.com/larksuite/cli/tar.gz/' + commit
    return {'commit': commit, 'tree': matches[0]['sha'], 'url': url,
            'sha256': hashlib.sha256(get_bytes(url)).hexdigest()}
