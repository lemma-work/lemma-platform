# Agent-facing login shells use this profile consistently. E2B's system services
# intentionally retain their base-image environment and /usr/bin/node.
export LEMMA_NODE_BINARY=/opt/node24/bin/node
export NODE_PATH=/opt/lemma-node/node_modules
export PNPM_HOME=/home/user/.local/share/pnpm
export DISPLAY=:99
export XDG_RUNTIME_DIR=/tmp/lemma-browser/runtime
export WORKSPACE_XVFB_SCREEN=1440x960x24
export AGENT_BROWSER_CONFIG=/tmp/lemma-browser/config.json
export AGENT_BROWSER_DASHBOARD_PORT=4848
export AGENT_BROWSER_DASHBOARD_INTERNAL_PORT=4849
export AGENT_BROWSER_EXECUTABLE_PATH=/usr/local/bin/workspace-chrome
export AGENT_BROWSER_PROFILE=/tmp/lemma-browser/profile
export AGENT_BROWSER_SESSION=workspace
export AGENT_BROWSER_HEADED=true
export MPLBACKEND=Agg
case ":${PATH}:" in
  *:/opt/node24/bin:*) ;;
  *) export PATH="/opt/node24/bin:${PATH}" ;;
esac
