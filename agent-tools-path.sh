# Sourced by login shells and the upstream non-interactive Bash environment.
# Keep Node available too: the npm-installed CLI entrypoints use /usr/bin/env node.
for _agent_tools_dir in /home/agent/.local/node-active /opt/agent-tools/bin; do
  case ":${PATH:-}:" in
    *:"$_agent_tools_dir":*) ;;
    *) PATH="$_agent_tools_dir${PATH:+:$PATH}" ;;
  esac
done
export PATH
unset _agent_tools_dir
