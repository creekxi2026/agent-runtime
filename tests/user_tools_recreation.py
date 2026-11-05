"""Offline package update fixture, checked in a second container using the same HOME."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

home = Path.home()
binary = home / '.local/bin/multica'
assert os.getuid() == 1000
if sys.argv[1] == 'update':
    # Valid executable with changed bytes: emulate a user's atomic CLI replacement.
    replacement = binary.with_name('multica-update-fixture')
    replacement.write_bytes(binary.read_bytes() + b'\nHOME-PERSISTENCE-FIXTURE\n')
    replacement.chmod(0o755)
    os.replace(replacement, binary)
    (home / 'multica-fixture.sha256').write_text(hashlib.sha256(binary.read_bytes()).hexdigest())
    fixture = home / 'workspace/update-fixture'
    fixture.mkdir()
    (fixture / 'package.json').write_text(json.dumps({'name': 'miniprogram-ci', 'version': '99.0.0',
        'main': 'index.js', 'bin': {'miniprogram-ci': 'index.js'}}))
    (fixture / 'index.js').write_text('#!/usr/bin/env node\nmodule.exports="persistent-user-version"; if(require.main===module) console.log(module.exports);\n')
    # Tarball forces an actual global package installation rather than an npm link.
    packed = json.loads(subprocess.check_output(['npm', 'pack', '--json', '--ignore-scripts'], cwd=fixture, text=True))[0]['filename']
    subprocess.run(['npm', 'install', '-g', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', str(fixture / packed)], check=True)
else:
    assert sys.argv[1] == 'check'
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == (home / 'multica-fixture.sha256').read_text()
    subprocess.run([str(binary), '--version'], check=True)
    assert subprocess.check_output(['miniprogram-ci'], text=True).strip() == 'persistent-user-version'
    assert subprocess.check_output(['node', '-p', 'require("miniprogram-ci")'], text=True).strip() == 'persistent-user-version'
    assert os.environ['MULTICA_CODEX_PATH'] == str(home / '.local/bin/codex')
    print('PASS user-replaced binary, npm CLI and SDK survive container recreation')
