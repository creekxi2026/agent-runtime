"""Offline installer fixtures; only apt/network and fixed image paths are isolated."""
import hashlib
import io
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import install_official as installer


def tar_bytes(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, content in files.items():
            data = content.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def write_deb(path, package):
    members = {'debian-binary': b'2.0\n', 'control.tar.gz': tar_bytes({
        'control': f'Package: {package}\nVersion: 1.0-1\nArchitecture: all\nMaintainer: Fixture <fixture@example.invalid>\nDescription: Offline installer test\n'}),
        'data.tar.gz': tar_bytes({})}
    with path.open('wb') as output:
        output.write(b'!<arch>\n')
        for name, data in members.items():
            header = f'{name + "/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(data):<10}`\n'
            output.write(header.encode() + data + (b'\n' if len(data) % 2 else b''))


def deb_package(path):
    # Read the real Debian ar/control archive without requiring host dpkg.
    with Path(path).open('rb') as source:
        assert source.read(8) == b'!<arch>\n'
        while header := source.read(60):
            size = int(header[48:58])
            data = source.read(size)
            if size % 2:
                source.read(1)
            if header[:16].decode().strip().rstrip('/') == 'control.tar.gz':
                with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
                    text = archive.extractfile('control').read().decode()
                    return text.split('Package: ', 1)[1].splitlines()[0] + '\n'
    raise AssertionError('Fixture has no control archive')


class ChromiumInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        packages = {}
        for name in ('chromium', 'chromium-common', 'chromium-sandbox'):
            deb = self.cache / (name + '.deb')
            write_deb(deb, name)
            packages[name] = {'version': '1.0-1', 'sha256': hashlib.sha256(deb.read_bytes()).hexdigest()}
        self.lock = {'chromium': {'arm64': packages}}
        self.paths = patch.object(installer, 'Path', side_effect=lambda p: self.cache if p == '/var/cache/apt/archives' else Path(p))
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.real_run = installer.subprocess.run
        self.apt = patch.object(installer.subprocess, 'run')
        self.commands = self.apt.start()
        self.addCleanup(self.apt.stop)
        real_metadata = installer.subprocess.check_output
        def metadata(args, **kwargs):
            if shutil.which('dpkg-deb'):
                with patch.object(installer.subprocess, 'run', self.real_run):
                    return real_metadata(args, **kwargs)
            return deb_package(args[2])
        self.metadata = patch.object(installer.subprocess, 'check_output', side_effect=metadata)
        self.metadata.start()
        self.addCleanup(self.metadata.stop)

    def assert_no_install(self):
        self.assertFalse(any('--no-download' in call.args[0] for call in self.commands.call_args_list))

    def test_missing_deb_fails_before_install(self):
        (self.cache / 'chromium-common.deb').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate.*chromium-common'):
            installer.install_chromium(self.lock, 'arm64')
        self.assert_no_install()

    def test_duplicate_deb_fails_before_install(self):
        shutil.copyfile(self.cache / 'chromium.deb', self.cache / 'duplicate.deb')
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate.*chromium'):
            installer.install_chromium(self.lock, 'arm64')
        self.assert_no_install()

    def test_checksum_mismatch_fails_before_install(self):
        self.lock['chromium']['arm64']['chromium-sandbox']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
            installer.install_chromium(self.lock, 'arm64')
        self.assert_no_install()

    def test_verified_package_family_installs_only_downloaded_debs(self):
        installer.install_chromium(self.lock, 'arm64')
        commands = [call.args[0] for call in self.commands.call_args_list]
        self.assertIn('APT::Update::Error-Mode=any', commands[0])
        self.assertIn('--download-only', commands[1])
        self.assertIn('--no-download', commands[2])


class NativeHelperInstallerTests(unittest.TestCase):
    def test_installs_native_copy_with_caller_chmod_ownership(self):
        import struct
        self.assertTrue(callable(getattr(installer, 'install_native_helper', None)), 'Native helper installer missing')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = root / 'zipalign'
            header = bytearray(20)
            header[:6] = b'\x7fELF\x02\x01'
            struct.pack_into('<H', header, 18, 183)
            native.write_bytes(header + b'native fixture')
            helper = root / 'lib/zipalign-linux'
            helper.parent.mkdir()
            helper.write_bytes(b'foreign fixture')
            library = helper.parent / 'lib64/libc++.so'
            library.parent.mkdir()
            library.write_bytes(b'foreign library')
            with patch.object(installer, 'install_debian_packages') as apt, \
                    patch.object(installer.os, 'chown') as ownership:
                installer.install_native_helper({'native_helpers': {'arm64': {'zipalign': {'version': '1'}}}},
                                                'arm64', native=native, helper=helper)
            self.assertEqual(helper.read_bytes(), native.read_bytes())
            self.assertFalse(library.exists())
            ownership.assert_called_once_with(helper, 1000, 1000)
            apt.assert_called_once()
            self.assertEqual(helper.stat().st_mode & 0o777, 0o755)


class SkillsInstallerTests(unittest.TestCase):
    def test_copies_complete_official_support_trees_from_verified_archive(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / 'installed'
            playwright = root / 'playwright-cli'
            (playwright / 'references').mkdir(parents=True)
            (playwright / 'SKILL.md').write_text('playwright fixture')
            (playwright / 'references/native.md').write_text('native reference')
            commit = 'a' * 40
            files = {f'cli-{commit}/skills/lark-doc/{name}': data for name, data in {
                'SKILL.md': 'lark fixture', 'references/detail.md': 'reference',
                'scripts/helper.py': 'print("fixture")', 'assets/template.json': '{}',
            }.items()}
            archive = root / 'fixture.tgz'
            archive.write_bytes(tar_bytes(files))
            artifact = {'commit': commit, 'url': 'https://codeload.github.com/larksuite/cli/tar.gz/' + commit,
                        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
            def offline_download(spec, destination):
                shutil.copyfile(archive, destination)
                installer.verify_file(destination, 'sha256', spec['sha256'])
                return destination
            copytree = shutil.copytree
            def local_copy(source, dest, *args, **kwargs):
                if str(source).startswith('/opt/agent-tools/'):
                    source = playwright
                return copytree(source, dest, *args, **kwargs)
            with patch.object(installer, 'Path', side_effect=lambda p: target if p == '/opt/agent-skills' else Path(p)), \
                    patch.object(installer, 'download', side_effect=offline_download), \
                    patch.object(installer.shutil, 'copytree', side_effect=local_copy):
                installer.install_skills({'official_skills': {'lark': artifact}})
            for name, data in files.items():
                self.assertEqual((target / 'lark-doc' / name.split('/lark-doc/')[1]).read_text(), data)
            self.assertEqual((target / 'playwright-cli/references/native.md').read_text(), 'native reference')
            self.assertEqual((target / 'inventory.json').read_text(), '["playwright-cli", "lark-doc"]\n')


if __name__ == '__main__':
    unittest.main()
