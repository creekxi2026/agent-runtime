#!/bin/sh
set -eu
# Root is only for first bind-directory ownership; never resolve its commands
# through a PATH containing the user's writable ~/.local/bin.
if [ "$(/usr/bin/id -u)" = 0 ]; then
  if [ "${HOME:-}" != /home/agent ]; then
    printf 'Expected HOME=/home/agent; use the matching Compose file.\n' >&2
    exit 78
  fi
  for dir in "$HOME" "$HOME/.codex" "$HOME/.agents" "$HOME/.local" "$HOME/.local/bin" "$HOME/workspace"; do
    if [ -L "$dir" ]; then
      printf 'Refusing symlink for private home directory: %s\n' "$dir" >&2
      exit 1
    fi
    /usr/bin/mkdir -p "$dir"
    /usr/bin/chown 1000:1000 "$dir"
    /usr/bin/chmod u+rwx "$dir"
  done
  exec /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups \
    --inh-caps=-all --ambient-caps=-all --bounding-set=-all --no-new-privs \
    /usr/bin/tini -- /usr/local/bin/agent-entrypoint "$@"
fi
exec /usr/bin/tini -- /usr/local/bin/agent-entrypoint "$@"
