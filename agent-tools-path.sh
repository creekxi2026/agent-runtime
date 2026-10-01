# Shared by login shells and non-interactive Bash. Do not source nvm.sh:
# installed Node is directly executable, and user npm prefix must stay in HOME.
_agent_home="${HOME:-/home/agent}"
_agent_prefix="$_agent_home/.local/bin:$_agent_home/.local/share/pnpm:$_agent_home/.local/share/pnpm/bin:/opt/agent-tools/bin:/opt/agent-upstream/.local/go/bin:/opt/agent-upstream/.local/share/pnpm/bin:/opt/agent-upstream/.local/node-active:/opt/agent-upstream/.local/bin:/app"
case "${PATH:-}" in
  "$_agent_prefix"|"$_agent_prefix":*) ;;
  *) PATH="$_agent_prefix${PATH:+:$PATH}" ;;
esac
export PATH
unset _agent_home _agent_prefix
