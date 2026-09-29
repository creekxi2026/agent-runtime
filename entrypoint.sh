#!/bin/sh
set -eu
umask 077
mkdir -p "$CODEX_HOME" "$HOME/workspace"
if [ "${1##*/}" = multica ] && [ "${2:-}" = daemon ]; then
  if [ -n "${MULTICA_SERVER_URL:-}" ]; then multica config set server_url "$MULTICA_SERVER_URL"; fi
  if [ -n "${MULTICA_APP_URL:-}" ]; then multica config set app_url "$MULTICA_APP_URL"; fi
  if [ -n "${MULTICA_BOOTSTRAP_TOKEN:-}" ]; then printf '%s\n' "$MULTICA_BOOTSTRAP_TOKEN" | multica login --token; fi
  if [ -n "${CODEX_BOOTSTRAP_API_KEY:-}" ]; then printf '%s\n' "$CODEX_BOOTSTRAP_API_KEY" | codex -c 'cli_auth_credentials_store="file"' login --with-api-key; fi
  if [ -n "${LARK_APP_SECRET:-}" ] && [ ! -e "$LARKSUITE_CLI_CONFIG_DIR/config.json" ]; then printf '%s\n' "$LARK_APP_SECRET" | lark-cli config init --app-id "${LARK_APP_ID:?Set LARK_APP_ID}" --app-secret-stdin --brand "${LARK_BRAND:-feishu}"; fi
fi
unset MULTICA_BOOTSTRAP_TOKEN CODEX_BOOTSTRAP_API_KEY LARK_APP_SECRET
exec "$@"
