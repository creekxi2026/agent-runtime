#!/usr/bin/env bash
# Test-only browser in a disposable peer container, never in the released image.
set -euo pipefail
: "${IMAGE:=agent-runtime:test}"
network="runtime-browser-test-$$"
server="$network-server"
cleanup() {
  docker rm -f "$server" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$network" >/dev/null
docker run -d --name "$server" --network "$network" --network-alias browser-peer \
  --user 0:0 --entrypoint /bin/sh --mount "type=bind,src=$PWD/tests,dst=/checks,readonly" \
  "$IMAGE" -ec 'playwright install --with-deps chromium --only-shell && exec node /checks/remote-browser-server.cjs' >/dev/null
ready=false
for attempt in $(seq 1 120); do
  if [ "$(docker inspect --format '{{.State.Running}}' "$server")" != true ]; then
    docker logs "$server"; exit 1
  fi
  if docker exec "$server" /opt/node/bin/node -e 'fetch("http://127.0.0.1:39232").then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))' >/dev/null 2>&1; then
    ready=true; break
  fi
  sleep 2
done
if [ "$ready" != true ]; then docker logs "$server"; exit 1; fi
# The browser-free runtime connects over the network and controls a real page.
docker run --rm -i --network "$network" --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /home/agent:uid=1000,gid=1000,mode=700 --tmpfs /tmp \
  "$IMAGE" node < tests/remote-browser-client.cjs
