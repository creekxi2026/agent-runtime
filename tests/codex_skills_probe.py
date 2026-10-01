"""Exercise real Codex discovery/config/account reads offline; no model calls."""
import errno
import json
import os
import uuid
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading

skill_dir = Path("/home/agent/.agents/skills/runtime-shared-smoke")
try:
    (skill_dir / "forbidden-write").write_text("must not succeed")
except OSError as error:
    assert error.errno == errno.EROFS, error
else:
    raise AssertionError("Shared skill mount is writable")

shared_codex = os.environ.get("CODEX_SHARED_DIR")
if shared_codex:
    shared_dir = Path(shared_codex)
    assert shared_dir == Path("/shared/codex"), "Unexpected shared Codex mount"
    try:
        (shared_dir / ("forbidden-write-" + uuid.uuid4().hex)).write_text("must not succeed")
    except OSError as error:
        assert error.errno == errno.EROFS, error
    else:
        raise AssertionError("Shared Codex mount is writable")
    codex_home = Path(os.environ["CODEX_HOME"])
    for name in ("config.toml", "auth.json"):
        target = codex_home / name
        assert target.is_symlink(), "Private Codex file is not linked: " + name
        assert os.readlink(target) == str(shared_dir / name), "Wrong Codex link: " + name
    auth = json.loads((codex_home / "auth.json").read_text())
    assert auth.get("OPENAI_API_KEY") == "INVALID-TEST-ONLY-NOT-A-REAL-KEY-" + sys.argv[1], "Stale test auth fixture"
    del auth

with tempfile.TemporaryFile(mode="w+") as errors:
    process = subprocess.Popen(
        ["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=errors, text=True, bufsize=1,
    )
    messages = queue.Queue()

    def read():
        for line in process.stdout:
            messages.put(json.loads(line))
        messages.put(None)

    threading.Thread(target=read, daemon=True).start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def reply(identifier):
        while True:
            message = messages.get(timeout=30)
            assert message is not None, "Codex exited without a reply"
            if message.get("id") == identifier:
                assert "error" not in message, "Codex request failed: " + str(identifier)
                return message["result"]

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "agent-runtime-smoke", "version": "1"},
        }})
        reply(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "skills/list", "params": {
            "cwds": ["/home/agent/workspace"], "forceReload": True,
        }})
        result = reply(2)
        found = [skill for entry in result["data"] for skill in entry["skills"]
                 if skill["name"] == "runtime-shared-smoke"]
        assert len(found) == 1, result
        assert found[0]["description"] == "Shared smoke revision-" + sys.argv[1], found
        assert found[0]["enabled"], found
        assert found[0]["path"] == str(skill_dir / "SKILL.md"), found
        print("PASS real Codex discovery, read-only shared skill:", sys.argv[1])
        if shared_codex:
            send({"id": 3, "method": "config/read", "params": {
                "includeLayers": False, "cwd": "/home/agent/workspace",
            }})
            config_result = reply(3)
            assert config_result["config"]["model"] == "runtime-fixture-" + sys.argv[1], "Stale shared model config"
            send({"id": 4, "method": "account/read", "params": {"refreshToken": False}})
            account_result = reply(4)
            assert account_result["account"]["type"] == "apiKey", "Expected API-key account type"
            print("PASS real Codex config parsing, API-key account type (not authentication), read-only shared Codex:", sys.argv[1])
    except BaseException:
        # Do not dump server responses/config/credentials in shared-auth mode.
        if not shared_codex:
            errors.seek(0)
            print(errors.read()[-3000:], file=sys.stderr)
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
