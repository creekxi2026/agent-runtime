"""Exercise real Codex app-server skill discovery offline, without model calls."""
import errno
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading

skill_dir = Path("/data/.agents/skills/runtime-shared-smoke")
try:
    (skill_dir / "forbidden-write").write_text("must not succeed")
except OSError as error:
    assert error.errno == errno.EROFS, error
else:
    raise AssertionError("Shared skill mount is writable")

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
                assert "error" not in message, message
                return message["result"]

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "agent-runtime-smoke", "version": "1"},
        }})
        reply(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "skills/list", "params": {
            "cwds": ["/data/workspace"], "forceReload": True,
        }})
        result = reply(2)
        found = [skill for entry in result["data"] for skill in entry["skills"]
                 if skill["name"] == "runtime-shared-smoke"]
        assert len(found) == 1, result
        assert found[0]["description"] == "Shared smoke revision-" + sys.argv[1], found
        assert found[0]["enabled"], found
        assert found[0]["path"] == str(skill_dir / "SKILL.md"), found
        print("PASS real Codex discovery, read-only shared skill:", sys.argv[1])
    except BaseException:
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
