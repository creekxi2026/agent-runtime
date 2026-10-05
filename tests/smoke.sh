#!/bin/sh
set -eu
: "${IMAGE:=agent-runtime:test}"
# Inspect and pin an already-local image before any probe; never pull.
IMAGE=$(docker image inspect "$IMAGE" --format '{{.Id}}')
export IMAGE
# Portable command timeout: macOS does not bundle GNU timeout.
timeout() { python3 -c 'import subprocess,sys;
try: sys.exit(subprocess.run(sys.argv[2:], timeout=float(sys.argv[1])).returncode)
except subprocess.TimeoutExpired: sys.exit(124)' "$@"; }
project="agent-smoke-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
shared=$(python3 tests/smoke_compose_owned.py init)
export SHARED_CODEX_DIR="$shared/codex"
reader_a=''
reader_b=''
shared_mode=false
docker() {
  if [ "$1" = run ]; then
    shift
    python3 tests/smoke_compose_owned.py docker-run "$shared" "$@"
  else
    command docker "$@"
  fi
}
compose() {
  p=$1; shift
  python3 tests/smoke_compose_owned.py run "$shared" "$p" "$shared_mode" "$@"
}
cleanup() {
  python3 tests/smoke_compose_owned.py cleanup "$shared"
}
trap cleanup EXIT INT TERM
# World-writable test fixture: EROFS must come from the mount, not UNIX modes.
mkdir -p "$SHARED_CODEX_DIR"
chmod 755 "$shared"
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
python3 tests/cleanup_uid_smoke.py
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
  chromium --version
  for tool in docker podman postgres firefox; do
    if command -v "$tool" >/dev/null 2>&1; then echo "Unexpected tool: $tool"; exit 16; fi
  done
  test ! -d /opt/agent-upstream
  test ! -d /ms-playwright
  # CLI update metadata is not an installed browser.
  node -e "const p=require(\"playwright\"),fs=require(\"node:fs\"); for(const b of [p.chromium,p.firefox,p.webkit]) if(fs.existsSync(b.executablePath())) process.exit(1)"
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
# sign() unconditionally chmods its helper. The supported Compose rootfs is
# writable; read-only rootfs tool-discovery above cannot exercise that contract.
docker run --rm -i --network none --user 1000:1000 --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /home/agent:uid=1000,gid=1000,mode=700 \
  --entrypoint python3 "$IMAGE" - < tests/native_helper_probe.py
compose "$project-a" run --rm runtime sh -ec 'printf a > /home/agent/workspace/tenant-a'
compose "$project-b" run --rm runtime sh -ec 'test ! -e /home/agent/workspace/tenant-a; printf b > /home/agent/workspace/tenant-b'
compose "$project-a" run --rm runtime sh -ec 'test -f /home/agent/workspace/tenant-a; test ! -e /home/agent/workspace/tenant-b'
# A local npm fixture exercises default user installation without a registry/key.
compose "$project-a" run --rm -T runtime python3 - install < tests/home_fixture.py
compose "$project-a" run --rm -T runtime python3 - check < tests/home_fixture.py
compose "$project-b" run --rm -T runtime python3 - isolated < tests/home_fixture.py
# Default startup leaves auth private across recreation and tenants.
compose "$project-a" run --rm runtime sh -ec '
  test -z "${CODEX_SHARED_DIR:-}"; test ! -e /shared/codex
  printf private-auth-fixture > "$CODEX_HOME/auth.json"
  mkdir -p "$CODEX_HOME/sessions"; printf private-session > "$CODEX_HOME/sessions/history"
'
compose "$project-a" run --rm runtime sh -ec '
  test ! -L "$CODEX_HOME/auth.json"
  test "$(cat "$CODEX_HOME/auth.json")" = private-auth-fixture
  test "$(cat "$CODEX_HOME/sessions/history")" = private-session
'
compose "$project-b" run --rm runtime sh -ec 'test ! -e "$CODEX_HOME/auth.json"; test ! -e "$CODEX_HOME/sessions/history"'
# Replace the intentionally non-JSON persistence marker with an invalid API key fixture.
compose "$project-a" run --rm runtime sh -ec 'printf "{\"auth_mode\":\"apikey\",\"OPENAI_API_KEY\":\"INVALID-TEST-ONLY\"}\n" > "$CODEX_HOME/auth.json"'
compose "$project-a" run --rm runtime sh -ec 'mkdir -p "$HOME/.agents/skills/runtime-shared-smoke"; printf "%s\n" "---" "name: runtime-shared-smoke" "description: Shared smoke revision-before" "---" > "$HOME/.agents/skills/runtime-shared-smoke/SKILL.md"'
compose "$project-a" run --rm -T runtime python3 - before < tests/codex_skills_probe.py
# Real local browser E2E uses the actual UID and native global defaults.
compose "$project-a" run --rm -T runtime python3 - < tests/local_browser_probe.py
compose "$project-a" run --rm -T runtime python3 - persist-write < tests/local_browser_probe.py
compose "$project-a" run --rm -T runtime python3 - persist-check < tests/local_browser_probe.py
compose "$project-b" run --rm -T runtime python3 - isolated < tests/local_browser_probe.py
compose "$project-a" run --rm -T runtime python3 - < tests/task_credentials_probe.py
# Opt in only after the private-HOME/browser probes.
shared_mode=true
# Keep both user containers alive while one host-side update reaches both.
reader_a=$(compose "$project-a" run --no-deps -d runtime sleep 300)
reader_b=$(compose "$project-b" run --no-deps -d runtime sleep 300)
for reader in "$reader_a" "$reader_b"; do
  timeout 30 docker exec --user 1000:1000 "$reader" sh -ec '
    while [ ! -L "$CODEX_HOME/config.toml" ] || [ ! -L "$CODEX_HOME/auth.json" ]; do sleep 0.1; done
  '
  docker exec --user 1000:1000 "$reader" sh -ec 'mkdir -p "$HOME/.agents/skills/runtime-shared-smoke"; printf "%s\n" "---" "name: runtime-shared-smoke" "description: Shared smoke revision-before" "---" > "$HOME/.agents/skills/runtime-shared-smoke/SKILL.md"'
  timeout 90 docker exec --user 1000:1000 -i "$reader" python3 - before < tests/codex_skills_probe.py
done
for reader in "$reader_a" "$reader_b"; do
  docker exec --user 1000:1000 "$reader" sh -ec 'printf "%s\n" "---" "name: runtime-shared-smoke" "description: Shared smoke revision-after" "---" > "$HOME/.agents/skills/runtime-shared-smoke/SKILL.md"'
done
write_codex_fixture after
for reader in "$reader_a" "$reader_b"; do
  timeout 90 docker exec --user 1000:1000 -i "$reader" python3 - after < tests/codex_skills_probe.py
done
compose "$project-a" run --rm -T runtime python3 - check < tests/home_fixture.py
compose "$project-a" run --rm -T runtime python3 - after < tests/codex_skills_probe.py
compose "$project-a" run --rm -T runtime python3 - after task < tests/codex_skills_probe.py
# No credential: a real daemon must refuse startup, never fabricate a login.
log=$(mktemp)
if timeout 30 python3 tests/smoke_compose_owned.py docker-run "$shared" --rm --network none --read-only --tmpfs /home/agent:uid=1000,gid=1000,mode=700 --tmpfs /tmp --cap-drop ALL "$IMAGE" >"$log" 2>&1; then
  cat "$log"; rm "$log"; exit 13
else
  status=$?
  test "$status" -ne 124
  grep -qi 'not authenticated' "$log"
fi
rm "$log"
# Exercise both explicit legacy bind storage and the default named-volume mode.
SHARED_CACHE_IMAGE="$IMAGE" SHARED_CACHE_STORAGE=bind python3 tests/shared_cache_smoke.py
SHARED_CACHE_IMAGE="$IMAGE" python3 tests/named_volume_smoke.py
printf '%s\n' 'PASS: offline tools, Go race/CGO, Python venv, persistent private HOME/tools, UID1000/caps-zero, official Codex skills discovery, local Chromium navigation/PNG/concurrent idle cleanup, shared Codex atomic updates (not authentication), private auth and optional read-only Codex policy, missing-auth rejection.'
