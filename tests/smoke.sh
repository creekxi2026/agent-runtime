#!/bin/sh
set -eu
: "${IMAGE:=agent-runtime:test}"
export IMAGE
project="agent-smoke-$$"
shared=$(mktemp -d)
export SHARED_SKILLS_DIR="$shared"
reader_a=''
reader_b=''
compose() { p=$1; shift; docker compose -f compose.yaml -f tests/compose.yaml --env-file runtime.env.example -p "$p" "$@"; }
cleanup() {
  if [ -n "$reader_a" ]; then docker rm -f "$reader_a" >/dev/null 2>&1 || true; fi
  if [ -n "$reader_b" ]; then docker rm -f "$reader_b" >/dev/null 2>&1 || true; fi
  compose "$project-a" down --volumes --remove-orphans >/dev/null 2>&1 || true
  compose "$project-b" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$shared"
}
trap cleanup EXIT INT TERM
# World-writable test fixture: EROFS must come from the mount, not UNIX modes.
mkdir -p "$shared/runtime-shared-smoke"
printf '%s\n' '---' 'name: runtime-shared-smoke' 'description: Shared smoke revision-before' '---' '# Shared smoke fixture' > "$shared/runtime-shared-smoke/SKILL.md"
chmod 755 "$shared"
chmod 777 "$shared/runtime-shared-smoke"
chmod 666 "$shared/runtime-shared-smoke/SKILL.md"
sh -n entrypoint.sh
python3 tests/test_entrypoint.py
docker compose --env-file runtime.env.example config --quiet
# With networking disabled, startup must not download or install anything.
docker run --rm --network none --read-only --tmpfs /data:uid=1000,gid=1000,mode=700 --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges "$IMAGE" sh -ec '
  test "$(id -u)" = 1000
  node --version; codex --version; multica --version; lark-cli --version; playwright-cli --version
  case "$(command -v codex)" in /data/*) exit 10 ;; esac
  case "$(command -v lark-cli)" in /data/*) exit 11 ;; esac
  test ! -S /tmp/podman.sock
  test ! -S /var/run/docker.sock
  if touch /rootfs-write-probe 2>/dev/null; then exit 12; fi
'
compose "$project-a" run --rm runtime sh -ec 'printf a > /data/workspace/tenant-a'
compose "$project-b" run --rm runtime sh -ec 'test ! -e /data/workspace/tenant-a; printf b > /data/workspace/tenant-b'
compose "$project-a" run --rm runtime sh -ec 'test -f /data/workspace/tenant-a; test ! -e /data/workspace/tenant-b'
# Keep both user containers alive while one host-side update reaches both.
reader_a=$(compose "$project-a" run --no-deps -d runtime sleep 300)
reader_b=$(compose "$project-b" run --no-deps -d runtime sleep 300)
for reader in "$reader_a" "$reader_b"; do
  timeout 90 docker exec -i "$reader" python3 - before < tests/codex_skills_probe.py
done
printf '%s\n' '---' 'name: runtime-shared-smoke' 'description: Shared smoke revision-after' '---' '# Shared smoke fixture' > "$shared/runtime-shared-smoke/SKILL.md"
for reader in "$reader_a" "$reader_b"; do
  timeout 90 docker exec -i "$reader" python3 - after < tests/codex_skills_probe.py
done
# No credential: a real daemon must refuse startup, never fabricate a login.
log=$(mktemp)
if timeout 30 docker run --rm --network none --read-only --tmpfs /data:uid=1000,gid=1000,mode=700 --tmpfs /tmp --cap-drop ALL "$IMAGE" >"$log" 2>&1; then
  cat "$log"; rm "$log"; exit 13
else
  status=$?
  test "$status" -ne 124
  grep -qi 'not authenticated' "$log"
fi
rm "$log"
printf '%s\n' 'PASS: offline startup, real tool binaries, no Podman/socket, non-root/read-only, per-user persistence, real Codex shared-skills discovery and live host update for two users, read-only shared mount, missing-auth rejection.'
