#!/bin/sh
set -eu
: "${IMAGE:=agent-runtime:test}"
export IMAGE
project="agent-smoke-$$"
shared=$(mktemp -d)
export SHARED_SKILLS_DIR="$shared/skills"
export SHARED_CODEX_DIR="$shared/codex"
reader_a=''
reader_b=''
compose() { p=$1; shift; DATA_DIR="$shared/$p-home" docker compose -f compose.yaml -f tests/compose.yaml -f tests/compose.readonly.yaml --env-file runtime.env.example -p "$p" "$@"; }
cleanup() {
  if [ -n "$reader_a" ]; then docker rm -f "$reader_a" >/dev/null 2>&1 || true; fi
  if [ -n "$reader_b" ]; then docker rm -f "$reader_b" >/dev/null 2>&1 || true; fi
  compose "$project-a" down --volumes --remove-orphans >/dev/null 2>&1 || true
  compose "$project-b" down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker run --rm --network none --user 0:0 --entrypoint /bin/sh \
    --mount "type=bind,src=$shared,dst=/fixture" "$IMAGE" -ec \
    'rm -rf "$1" "$2"' cleanup "/fixture/$project-a-home" "/fixture/$project-b-home" >/dev/null
  rm -rf "$shared"
}
trap cleanup EXIT INT TERM
# World-writable test fixture: EROFS must come from the mount, not UNIX modes.
mkdir -p "$SHARED_SKILLS_DIR/runtime-shared-smoke" "$SHARED_CODEX_DIR"
printf '%s\n' '---' 'name: runtime-shared-smoke' 'description: Shared smoke revision-before' '---' '# Shared smoke fixture' > "$SHARED_SKILLS_DIR/runtime-shared-smoke/SKILL.md"
chmod 755 "$shared" "$SHARED_SKILLS_DIR"
chmod 777 "$SHARED_CODEX_DIR"
# Invalid local-only fixtures: never call a model or claim authentication.
write_codex_fixture() {
  python3 - "$1" <<'PY'
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["SHARED_CODEX_DIR"])
revision = sys.argv[1]
files = {
    "config.toml": 'cli_auth_credentials_store = "file"\nmodel = "runtime-fixture-' + revision + '"\n',
    "auth.json": json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY":
                              "INVALID-TEST-ONLY-NOT-A-REAL-KEY-" + revision}) + "\n",
}
for name, content in files.items():
    temporary = root / (name + ".tmp")
    temporary.write_text(content)
    temporary.chmod(0o666)
    os.replace(temporary, root / name)
PY
}
write_codex_fixture before
chmod 777 "$SHARED_SKILLS_DIR/runtime-shared-smoke"
chmod 666 "$SHARED_SKILLS_DIR/runtime-shared-smoke/SKILL.md"
sh -n entrypoint.sh
sh -n bootstrap.sh
sh -n agent-tools-path.sh
python3 tests/test_entrypoint.py
docker compose --env-file runtime.env.example config --quiet
# With networking disabled, startup must not download or install anything.
docker run --rm --network none --read-only --tmpfs /home/agent:uid=1000,gid=1000,mode=700 --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges "$IMAGE" sh -ec '
  test "$(id -u)" = 1000
  node --version; codex --version; multica --version; lark-cli --version; playwright-cli --version
  miniprogram-ci --version
  miniprogram-ci --help >/dev/null
  go version; uv --version; pnpm --version
  for tool in git gh ssh curl jq rg rsync zip unzip python3 gcc g++ make pkg-config; do command -v "$tool"; done
  for tool in docker podman postgres chromium firefox; do
    if command -v "$tool" >/dev/null 2>&1; then echo "Unexpected tool: $tool"; exit 16; fi
  done
  test ! -d /opt/agent-upstream
  test ! -d /ms-playwright
  test ! -d /home/agent/.cache/ms-playwright
  python3 -m json.tool /usr/share/agent-runtime/dependencies.json >/dev/null
  test "$(command -v bwrap)" = /usr/bin/bwrap
  bwrap --version
  case "$(command -v codex)" in /home/agent/*) exit 10 ;; esac
  case "$(command -v lark-cli)" in /home/agent/*) exit 11 ;; esac
  test ! -S /tmp/podman.sock
  test ! -S /var/run/docker.sock
  if touch /rootfs-write-probe 2>/dev/null; then exit 12; fi
'
# Exercise terminal/agent shell modes, not only Docker's inherited ENV PATH.
for mode in -lc -ic -ilc; do
  printf 'Checking tool PATH with bash %s\n' "$mode"
  docker run --rm --network none --read-only --tmpfs /home/agent:uid=1000,gid=1000,mode=700 --tmpfs /tmp \
    --cap-drop ALL --security-opt no-new-privileges --entrypoint /bin/bash "$IMAGE" "$mode" '
    set -eu
    mkdir -p "$CODEX_HOME"
    for tool in node codex lark-cli playwright-cli miniprogram-ci; do command -v "$tool"; done
    case ":$PATH:" in *:/opt/agent-tools/bin:*) ;; *) exit 14 ;; esac
    case ":$PATH:" in *:/opt/node/bin:*) ;; *) exit 15 ;; esac
    lark-cli --help >/dev/null
    miniprogram-ci --version
    miniprogram-ci --help >/dev/null
  '
done
compose "$project-a" run --rm runtime sh -ec 'printf a > /home/agent/workspace/tenant-a'
compose "$project-b" run --rm runtime sh -ec 'test ! -e /home/agent/workspace/tenant-a; printf b > /home/agent/workspace/tenant-b'
compose "$project-a" run --rm runtime sh -ec 'test -f /home/agent/workspace/tenant-a; test ! -e /home/agent/workspace/tenant-b'
# A local npm fixture exercises default user installation without a registry/key.
compose "$project-a" run --rm -T runtime python3 - install < tests/home_fixture.py
compose "$project-a" run --rm -T runtime python3 - check < tests/home_fixture.py
compose "$project-b" run --rm -T runtime python3 - isolated < tests/home_fixture.py
# The delivered Compose permits shared writes, still under agent identity.
DATA_DIR="$shared/$project-a-home" docker compose -f compose.yaml -f tests/compose.yaml \
  --env-file runtime.env.example -p "$project-a" run --rm runtime sh -ec '
    test "$(id -u)" = 1000
    printf writable > /shared/codex/write-probe
    printf writable > /home/agent/.agents/skills/runtime-shared-smoke/write-probe
    rm /shared/codex/write-probe /home/agent/.agents/skills/runtime-shared-smoke/write-probe
  '
# Real remote-browser verification is a separate CI step; browsers never enter this image.
# Keep both user containers alive while one host-side update reaches both.
reader_a=$(compose "$project-a" run --no-deps -d runtime sleep 300)
reader_b=$(compose "$project-b" run --no-deps -d runtime sleep 300)
for reader in "$reader_a" "$reader_b"; do
  timeout 90 docker exec --user 1000:1000 -i "$reader" python3 - before < tests/codex_skills_probe.py
done
printf '%s\n' '---' 'name: runtime-shared-smoke' 'description: Shared smoke revision-after' '---' '# Shared smoke fixture' > "$SHARED_SKILLS_DIR/runtime-shared-smoke/SKILL.md"
write_codex_fixture after
for reader in "$reader_a" "$reader_b"; do
  timeout 90 docker exec --user 1000:1000 -i "$reader" python3 - after < tests/codex_skills_probe.py
done
compose "$project-a" run --rm -T runtime python3 - check < tests/home_fixture.py
# No credential: a real daemon must refuse startup, never fabricate a login.
log=$(mktemp)
if timeout 30 docker run --rm --network none --read-only --tmpfs /home/agent:uid=1000,gid=1000,mode=700 --tmpfs /tmp --cap-drop ALL "$IMAGE" >"$log" 2>&1; then
  cat "$log"; rm "$log"; exit 13
else
  status=$?
  test "$status" -ne 124
  grep -qi 'not authenticated' "$log"
fi
rm "$log"
printf '%s\n' 'PASS: offline startup, self-built tools, Go race/CGO, Python venv, rsync, empty HOME mounts and persistent user installs, no Podman/socket, non-root/read-only, per-user persistence, real Codex shared-skills/config parsing and API-key account type (not authentication), atomic shared-file update for two users, writable default and optional read-only shared mounts, missing-auth rejection.'
