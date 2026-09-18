# Agent-facing login shells use this profile consistently. E2B's system services
# intentionally retain their base-image environment and /usr/bin/node.
export LEMMA_NODE_BINARY=/opt/node24/bin/node
export NODE_PATH=/opt/lemma-node/node_modules
export PNPM_HOME=/home/user/.local/share/pnpm
export DISPLAY=:99
export XDG_RUNTIME_DIR=/tmp/lemma-browser/runtime
export WORKSPACE_XVFB_SCREEN=1440x960x24
# The ceiling on a viewer-requested resize. Exported here as well as set on
# the template, because a login shell on E2B does not see the template's own
# environment -- and `start-browser` runs from one.
export WORKSPACE_XVFB_MAX_SCREEN=1920x1200x24
export AGENT_BROWSER_CONFIG=/tmp/lemma-browser/config.json
export AGENT_BROWSER_EXECUTABLE_PATH=/usr/local/bin/workspace-chrome
# The durable profile: in the home, so a login outlives the sandbox's
# compute. Must match `sandbox_runtime/paths.py`.
export AGENT_BROWSER_PROFILE=/home/user/.lemma/browser/profile
export AGENT_BROWSER_SESSION=workspace
export AGENT_BROWSER_HEADED=true
# The daemon closes Chrome after this long without a command. Set here too so a
# browser started from an agent's login shell is bounded the same way one
# started by the runtime is.
export AGENT_BROWSER_IDLE_TIMEOUT_MS=300000
export MPLBACKEND=Agg
# pnpm installs global binaries into PNPM_HOME, which was on no PATH at all
# on this fabric -- so `pnpm add -g` succeeded and produced something the
# agent could not then run.
case ":${PATH}:" in
  *:${PNPM_HOME}:*) ;;
  *) export PATH="${PNPM_HOME}:${PATH}" ;;
esac
case ":${PATH}:" in
  *:/opt/node24/bin:*) ;;
  *) export PATH="/opt/node24/bin:${PATH}" ;;
esac
