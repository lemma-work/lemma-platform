#!/usr/bin/env bash
set -euo pipefail

DISPLAY_VALUE="${DISPLAY:-:99}"
SCREEN="${WORKSPACE_XVFB_SCREEN:-1440x960x24}"
DASHBOARD_PORT="${AGENT_BROWSER_DASHBOARD_PORT:-4848}"
DASHBOARD_INTERNAL_PORT="${AGENT_BROWSER_DASHBOARD_INTERNAL_PORT:-$((DASHBOARD_PORT + 1))}"
PROFILE_DIR="${AGENT_BROWSER_PROFILE:-/tmp/agentbox-browser/profile}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/agentbox-browser/runtime}"
CONFIG_PATH="${AGENT_BROWSER_CONFIG:-/tmp/agentbox-browser/config.json}"
EXECUTABLE_PATH="${AGENT_BROWSER_EXECUTABLE_PATH:-/usr/local/bin/workspace-chrome}"
DISPLAY_NUMBER="${DISPLAY_VALUE#:}"
DISPLAY_NUMBER="${DISPLAY_NUMBER%%.*}"
HOME_DIR="${HOME:-/home/appuser}"
if ! mkdir -p "$HOME_DIR" 2>/dev/null || [ ! -w "$HOME_DIR" ]; then
  HOME_DIR="/tmp/agentbox-home-${UID:-10001}"
  mkdir -p "$HOME_DIR"
fi
NPM_CACHE_DIR="${NPM_CONFIG_CACHE:-$HOME_DIR/.npm}"
if ! mkdir -p "$NPM_CACHE_DIR" 2>/dev/null || [ ! -w "$NPM_CACHE_DIR" ]; then
  NPM_CACHE_DIR="/tmp/agentbox-npm-${UID:-10001}"
  mkdir -p "$NPM_CACHE_DIR"
fi

export HOME="$HOME_DIR"
export NPM_CONFIG_CACHE="$NPM_CACHE_DIR"
export DISPLAY="$DISPLAY_VALUE"
export AGENT_BROWSER_HEADED="${AGENT_BROWSER_HEADED:-true}"
export AGENT_BROWSER_PROFILE="$PROFILE_DIR"
export AGENT_BROWSER_SESSION="${AGENT_BROWSER_SESSION:-workspace}"
unset AGENT_BROWSER_SESSION_NAME

mkdir -p "$PROFILE_DIR" /tmp/.X11-unix
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
if ! mkdir -p "$RUNTIME_DIR" 2>/dev/null || [ ! -w "$RUNTIME_DIR" ]; then
  RUNTIME_DIR="/tmp/agent-browser-runtime-${UID:-10001}"
  mkdir -p "$RUNTIME_DIR"
fi
export XDG_RUNTIME_DIR="$RUNTIME_DIR"

if [ ! -S "/tmp/.X11-unix/X${DISPLAY_NUMBER}" ]; then
  rm -f "/tmp/.X${DISPLAY_NUMBER}-lock"
  nohup Xvfb "$DISPLAY_VALUE" -screen 0 "$SCREEN" -ac +extension RANDR \
    >/tmp/agentbox-xvfb.log 2>&1 &
  sleep 0.4
fi

agent-browser dashboard start --port "$DASHBOARD_INTERNAL_PORT" >/tmp/agent-browser-dashboard.log 2>&1 || true
if ! pgrep -f "socat.*TCP-LISTEN:${DASHBOARD_PORT}" >/dev/null 2>&1; then
  nohup socat TCP-LISTEN:"$DASHBOARD_PORT",fork,reuseaddr,bind=0.0.0.0 \
    TCP:127.0.0.1:"$DASHBOARD_INTERNAL_PORT" \
    >/tmp/agent-browser-dashboard-forwarder.log 2>&1 &
fi

if [ "$#" -gt 0 ]; then
  exec agent-browser open "$@"
fi

exec agent-browser open
