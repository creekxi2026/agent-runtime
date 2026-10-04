"""Real miniprogram-ci helper contract, run as the application UID on Linux."""
import hashlib
import os
from pathlib import Path
import platform
import struct
import subprocess
import tempfile
import zipfile

HELPER = Path('/opt/agent-tools/node_modules/miniprogram-ci/dist/ci/android-miniapp-toolkit/lib/zipalign-linux')


def probe():
    assert os.getuid() == 1000 and os.getgid() == 1000
    header = HELPER.read_bytes()[:20]
    machine = {'aarch64': 183, 'x86_64': 62}[platform.machine()]
    assert header[:4] == b'\x7fELF' and header[5] == 1
    assert struct.unpack('<H', header[18:20])[0] == machine, 'Foreign zipalign helper'
    before = hashlib.sha256(HELPER.read_bytes()).hexdigest()
    # sign() chmods unconditionally, even when it is already executable.
    HELPER.chmod(0o755)
    HELPER.chmod(0o755)
    assert not (HELPER.parent / 'lib64/libc++.so').exists()
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'fixture.apk'
        aligned = Path(directory) / 'aligned.apk'
        files = {'assets/plain.txt': b'fixture unchanged', 'assets/binary.bin': bytes(range(256)),
                 'assets/compressed.txt': b'compressible content' * 80}
        with zipfile.ZipFile(source, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED if 'compressed' in name else zipfile.ZIP_STORED)
        subprocess.run([str(HELPER), '-f', '-v', '4', str(source), str(aligned)], check=True, timeout=30)
        subprocess.run([str(HELPER), '-c', '-v', '4', str(aligned)], check=True, timeout=30)
        with zipfile.ZipFile(aligned) as archive:
            assert set(archive.namelist()) == set(files)
            assert {name: archive.read(name) for name in files} == files
        assert before == hashlib.sha256(HELPER.read_bytes()).hexdigest()
    print('PASS UID1000 native zipalign unconditional chmod, -f -v 4, alignment/content contract', platform.machine(), before)


if __name__ == '__main__':
    probe()
