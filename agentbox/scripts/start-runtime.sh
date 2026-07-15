#!/usr/bin/env bash
set -euo pipefail

DISPLAY_VALUE="${DISPLAY:-:99}"
SCREEN="${WORKSPACE_XVFB_SCREEN:-1440x960x24}"
DASHBOARD_PORT="${AGENT_BROWSER_DASHBOARD_PORT:-4848}"
DASHBOARD_INTERNAL_PORT="${AGENT_BROWSER_DASHBOARD_INTERNAL_PORT:-$((DASHBOARD_PORT + 1))}"
FUNCTION_EXECUTOR_PORT="${AGENTBOX_FUNCTION_EXECUTOR_PORT:-8090}"
PROFILE_DIR="${AGENT_BROWSER_PROFILE:-/tmp/agentbox-browser/profile}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/agentbox-browser/runtime}"
CONFIG_PATH="${AGENT_BROWSER_CONFIG:-/tmp/agentbox-browser/config.json}"
EXECUTABLE_PATH="${AGENT_BROWSER_EXECUTABLE_PATH:-/usr/local/bin/workspace-chrome}"
DISPLAY_NUMBER="${DISPLAY_VALUE#:}"
DISPLAY_NUMBER="${DISPLAY_NUMBER%%.*}"
HOME_DIR="${HOME:-/home/appuser}"

if [ ! -w "$HOME_DIR" ]; then
  HOME_DIR="/home/appuser"
fi

export HOME="$HOME_DIR"
export DISPLAY="$DISPLAY_VALUE"
export AGENT_BROWSER_HEADED="${AGENT_BROWSER_HEADED:-true}"
export AGENT_BROWSER_PROFILE="$PROFILE_DIR"
export AGENT_BROWSER_SESSION="${AGENT_BROWSER_SESSION:-workspace}"
export XDG_RUNTIME_DIR="$RUNTIME_DIR"
unset AGENT_BROWSER_SESSION_NAME

# Browser credentials, cookies, caches, and lock files are compute-ephemeral.
# A retained provider disk may preserve /tmp, so clear this state whenever the
# runtime process starts after a stop/resume. Remove the legacy workspace paths
# once so an upgraded retained sandbox cannot silently reuse old credentials.
case "$PROFILE_DIR" in
  /tmp/*) rm -rf -- "$PROFILE_DIR" ;;
esac
case "$CONFIG_PATH" in
  /tmp/*) rm -f -- "$CONFIG_PATH" ;;
esac
rm -rf -- "$HOME_DIR/.agent-browser" /workspace/.browser-profile
rm -f -- /workspace/agent-browser.json
mkdir -p "$PROFILE_DIR" "$XDG_RUNTIME_DIR" /tmp/.X11-unix
rm -f \
  "$PROFILE_DIR/SingletonCookie" \
  "$PROFILE_DIR/SingletonLock" \
  "$PROFILE_DIR/SingletonSocket" \
  "$PROFILE_DIR/DevToolsActivePort"
if [ ! -f "$CONFIG_PATH" ]; then
  mkdir -p "$(dirname "$CONFIG_PATH")"
  cat > "$CONFIG_PATH" <<EOF
{
  "headed": true,
  "profile": "$PROFILE_DIR",
  "executablePath": "$EXECUTABLE_PATH",
  "args": "--no-sandbox,--disable-dev-shm-usage,--no-first-run,--no-default-browser-check"
}
EOF
fi

if [ ! -S "/tmp/.X11-unix/X${DISPLAY_NUMBER}" ]; then
  rm -f "/tmp/.X${DISPLAY_NUMBER}-lock"
  nohup Xvfb "$DISPLAY_VALUE" -screen 0 "$SCREEN" -ac +extension RANDR \
    >/tmp/agentbox-xvfb.log 2>&1 &
  sleep 0.5
fi

agent-browser dashboard start --port "$DASHBOARD_INTERNAL_PORT" \
  >/tmp/agent-browser-dashboard.log 2>&1 || true

if ! pgrep -f "socat.*TCP-LISTEN:${DASHBOARD_PORT}" >/dev/null 2>&1; then
  nohup socat TCP-LISTEN:"$DASHBOARD_PORT",fork,reuseaddr,bind=0.0.0.0 \
    TCP:127.0.0.1:"$DASHBOARD_INTERNAL_PORT" \
    >/tmp/agent-browser-dashboard-forwarder.log 2>&1 &
fi

if ! pgrep -f "uvicorn agentbox.function_executor:app.*--port ${FUNCTION_EXECUTOR_PORT}" >/dev/null 2>&1; then
  nohup python -m uvicorn agentbox.function_executor:app \
    --host 0.0.0.0 \
    --port "$FUNCTION_EXECUTOR_PORT" \
    >/tmp/agentbox-function-executor.log 2>&1 &
fi

# Pre-warm the lemma CLI once boot has settled: the first `lemma` invocation
# on a node that just pulled the image otherwise pays cold reads for every
# .pyc through gVisor's gofer (seconds of wall time at near-zero CPU). The
# delay keeps the warmup out of the boot window — the CPU quota is shared and
# CFS throttling is niceness-blind, so running it at boot would slow down an
# early user command instead of helping it.
nohup bash -c 'sleep 15; lemma --help >/dev/null 2>&1' >/dev/null 2>&1 &

exec python -m agentbox.runtime_server
