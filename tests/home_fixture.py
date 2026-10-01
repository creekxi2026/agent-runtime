"""Real offline npm user install and reconstruction check; no real credentials."""
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

home = Path(os.environ["HOME"])
assert home == Path("/home/agent")
assert pwd.getpwuid(os.getuid()).pw_dir == str(home)
assert os.getuid() == 1000 and os.getgid() == 1000
assert os.path.samefile("/data", home), "legacy path must alias the same home, never a second store"
assert subprocess.check_output(["npm", "prefix", "-g"], text=True).strip() == str(home / ".local")
for process in ("1", "self"):
    status = dict(line.split(":", 1) for line in Path("/proc", process, "status").read_text().splitlines() if ":" in line)
    assert status["Uid"].split() == ["1000"] * 4
    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        assert int(status[key].strip(), 16) == 0, (process, key)
    assert status["NoNewPrivs"].strip() == "1"
assert not os.access("/opt/agent-upstream", os.W_OK)
assert not os.access("/opt/agent-tools", os.W_OK)
mode = sys.argv[1]
if mode == "install":
    package = home / "workspace/.home-install-fixture"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"name": "runtime-home-fixture", "version": "1.0.0", "bin": {"runtime-home-probe": "cli.js"}}))
    (package / "cli.js").write_text('#!/usr/bin/env node\nconsole.log("persistent-home-tool");\n')
    (package / "cli.js").chmod(0o755)
    subprocess.run(["npm", "install", "-g", "--offline", "--no-audit", "--no-fund", str(package)], check=True, timeout=30)
    # Exercise the relocated Go compiler and its standard library, not only --version.
    (package / "hello.go").write_text('package main\nimport "fmt"\nfunc main() { fmt.Println("relocated-go-ok") }\n')
    assert subprocess.check_output(["go", "run", str(package / "hello.go")], text=True, timeout=90).strip() == "relocated-go-ok"
if mode in ("install", "check"):
    assert (home / ".local/bin/runtime-home-probe").exists()
    assert subprocess.check_output(["runtime-home-probe"], text=True).strip() == "persistent-home-tool"
else:
    assert mode == "isolated"
    assert not (home / ".local/bin/runtime-home-probe").exists()
# Shared mounts belong to the administrator; private state must not be root-owned.
for base, directories, files in os.walk(home, followlinks=False):
    directories[:] = [name for name in directories if Path(base, name) != home / ".agents/skills"]
    for name in files + directories:
        path = Path(base, name)
        assert path.lstat().st_uid == 1000, "Unexpected owner in private HOME: " + str(path)
print("PASS unified passwd/HOME, dropped capabilities, immutable tools and user installation:", mode)
