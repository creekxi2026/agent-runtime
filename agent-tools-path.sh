# Login shells and non-interactive Bash share this PATH. Skip nvm.sh:
# Node runs directly; the npm prefix must remain in HOME.
_agent_home="${HOME:-/home/agent}"
_agent_prefix="$_agent_home/.local/bin:$_agent_home/.local/share/pnpm:$_agent_home/.local/share/pnpm/bin:/opt/agent-tools/bin:/opt/node/bin:/opt/go/bin:/usr/local/bin"
case "${PATH:-}" in
  "$_agent_prefix"|"$_agent_prefix":*) ;;
  *) PATH="$_agent_prefix${PATH:+:$PATH}" ;;
esac
export PATH
unset _agent_home _agent_prefix
