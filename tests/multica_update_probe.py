"""Offline replacement probe in a disposable container, never a live daemon."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

assert os.getuid() == 1000
binary = Path(shutil.which("multica"))
assert binary == Path("/opt/multica/bin/multica"), binary
assert not os.access("/usr/local/bin", os.W_OK)
for name in ("agent-bootstrap", "agent-entrypoint", "gh", "uv"):
    assert not os.access(Path("/usr/local/bin") / name, os.W_OK), name
before = subprocess.check_output([str(binary), "--version"])
temporary = None
try:
    with tempfile.NamedTemporaryFile(prefix="multica-update-", dir=binary.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(binary.read_bytes())
    temporary.chmod(0o755)
    os.replace(temporary, binary)
    assert subprocess.check_output([str(binary), "--version"]) == before
finally:
    if temporary is not None:
        temporary.unlink(missing_ok=True)
print("PASS unprivileged Multica atomic replacement; system tools remain protected")
