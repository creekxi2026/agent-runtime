#!/bin/sh
set -eu
umask 077
mkdir -p "$CODEX_HOME" "$HOME/workspace" "$HOME/.local/bin" "$HOME/.local/share" "$HOME/.cache"
if [ -n "${CODEX_SHARED_DIR:-}" ]; then
  # Preflight both files before moving any private config or credentials.
  for name in config.toml auth.json; do
    source="$CODEX_SHARED_DIR/$name"
    target="$CODEX_HOME/$name"
    if [ ! -f "$source" ] || [ ! -r "$source" ]; then
      printf 'Shared Codex file missing or unreadable: %s\n' "$source" >&2
      exit 1
    fi
    if [ -L "$target" ] && [ "$(readlink "$target")" = "$source" ]; then continue; fi
    if [ -e "$target.before-shared" ] || [ -L "$target.before-shared" ]; then
      printf 'Refusing to overwrite private Codex backup: %s\n' "$target.before-shared" >&2
      exit 1
    fi
  done
  for name in config.toml auth.json; do
    source="$CODEX_SHARED_DIR/$name"
    target="$CODEX_HOME/$name"
    if [ -L "$target" ] && [ "$(readlink "$target")" = "$source" ]; then continue; fi
    if [ -e "$target" ] || [ -L "$target" ]; then mv -n "$target" "$target.before-shared"; fi
    ln -s "$source" "$target"
  done
fi
if [ "${1##*/}" = multica ] && [ "${2:-}" = daemon ]; then
  if [ -n "${MULTICA_SERVER_URL:-}" ]; then multica config set server_url "$MULTICA_SERVER_URL"; fi
  if [ -n "${MULTICA_APP_URL:-}" ]; then multica config set app_url "$MULTICA_APP_URL"; fi
  if [ -n "${MULTICA_BOOTSTRAP_TOKEN:-}" ]; then printf '%s\n' "$MULTICA_BOOTSTRAP_TOKEN" | multica login --token; fi
  if [ -z "${CODEX_SHARED_DIR:-}" ] && [ -n "${CODEX_BOOTSTRAP_API_KEY:-}" ]; then printf '%s\n' "$CODEX_BOOTSTRAP_API_KEY" | codex -c 'cli_auth_credentials_store="file"' login --with-api-key; fi
  if [ -n "${LARK_APP_SECRET:-}" ] && [ ! -e "$LARKSUITE_CLI_CONFIG_DIR/config.json" ]; then printf '%s\n' "$LARK_APP_SECRET" | lark-cli config init --app-id "${LARK_APP_ID:?Set LARK_APP_ID}" --app-secret-stdin --brand "${LARK_BRAND:-feishu}"; fi
fi
unset MULTICA_BOOTSTRAP_TOKEN CODEX_BOOTSTRAP_API_KEY LARK_APP_SECRET
exec "$@"
