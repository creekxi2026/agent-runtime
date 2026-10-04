#!/usr/bin/env python3
"""Install locked Chromium debs and complete official skills during image build."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from install_tools import download, extract, verify_file


def install_chromium(lock, arch):
    packages = lock['chromium'][arch]
    if set(packages) != {'chromium', 'chromium-common', 'chromium-sandbox'}:
        raise ValueError('Expected Chromium package family')
    install_debian_packages(packages)


def install_debian_packages(packages):
    specs = []
    for name, item in packages.items():
        if not re.fullmatch(r'[0-9A-Za-z.+:~_-]+', item['version']):
            raise ValueError('Invalid locked Debian version')
        specs.append(name + '=' + item['version'])
    subprocess.run(['apt-get', '-o', 'APT::Update::Error-Mode=any', 'update'], check=True)
    subprocess.run(['apt-get', 'install', '-y', '--no-install-recommends', '--download-only', *specs], check=True)
    for name, item in packages.items():
        matches = []
        for deb in Path('/var/cache/apt/archives').glob('*.deb'):
            identity = subprocess.check_output(['dpkg-deb', '-f', str(deb), 'Package'], text=True).strip()
            if identity == name: matches.append(deb)
        if len(matches) != 1: raise ValueError('Missing or duplicate downloaded deb: ' + name)
        verify_file(matches[0], 'sha256', item['sha256'])
    subprocess.run(['apt-get', 'install', '-y', '--no-install-recommends', '--no-download', *specs], check=True)


def install_native_helper(lock, arch, *, native=Path('/usr/bin/zipalign'),
                          helper=Path('/opt/agent-tools/node_modules/miniprogram-ci/dist/ci/android-miniapp-toolkit/lib/zipalign-linux')):
    install_debian_packages(lock['native_helpers'][arch])
    header = native.read_bytes()[:20]
    if (header[:6] != b'\x7fELF\x02\x01' or
            struct.unpack('<H', header[18:20])[0] != {'amd64': 62, 'arm64': 183}[arch]):
        raise ValueError('Wrong native zipalign architecture')
    shutil.copyfile(native, helper)
    helper.chmod(0o755)
    # The released caller chmods this exact file on every sign operation. Only
    # this file is user-owned; its parent/tool tree stays root-owned and locked.
    os.chown(helper, 1000, 1000)
    (helper.parent / 'lib64/libc++.so').unlink(missing_ok=True)


def install_skills(lock):
    target = Path('/opt/agent-skills')
    target.mkdir()
    shutil.copytree('/opt/agent-tools/node_modules/@playwright/cli/skills/playwright-cli', target / 'playwright-cli')
    artifact = lock['official_skills']['lark']
    if not re.fullmatch(r'[0-9a-f]{40}', artifact['commit']): raise ValueError('Invalid skills commit')
    if artifact['url'] != 'https://codeload.github.com/larksuite/cli/tar.gz/' + artifact['commit']:
        raise ValueError('Unexpected official skills URL')
    with tempfile.TemporaryDirectory(prefix='agent-skills-') as d:
        root = Path(d)
        archive = download(artifact, root / 'skills.tgz')
        extract(archive, root / 'source')
        source = root / 'source' / ('cli-' + artifact['commit']) / 'skills'
        skills = sorted(p for p in source.iterdir() if p.is_dir() and (p / 'SKILL.md').is_file())
        if not skills: raise ValueError('Official source has no skills')
        for skill in skills: shutil.copytree(skill, target / skill.name)
        (target / 'inventory.json').write_text(json.dumps(['playwright-cli'] + [s.name for s in skills]) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--arch', required=True)
    parser.add_argument('--mode', choices=['chromium','skills','native-helper'], required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    if args.mode == 'chromium': install_chromium(manifest, args.arch)
    elif args.mode == 'native-helper': install_native_helper(manifest, args.arch)
    else: install_skills(manifest)
