#!/usr/bin/env bash
set -euo pipefail

DISPLAY_VALUE="${DISPLAY:-:99}"
SCREEN="${WORKSPACE_XVFB_SCREEN:-1440x960x24}"
DASHBOARD_PORT="${AGENT_BROWSER_DASHBOARD_PORT:-4848}"
DASHBOARD_INTERNAL_PORT="${AGENT_BROWSER_DASHBOARD_INTERNAL_PORT:-$((DASHBOARD_PORT + 1))}"
FUNCTION_EXECUTOR_PORT="${AGENTBOX_FUNCTION_EXECUTOR_PORT:-8090}"
PROFILE_DIR="${AGENT_BROWSER_PROFILE:-/workspace/.browser-profile}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/workspace-runtime}"
CONFIG_PATH="${AGENT_BROWSER_CONFIG:-/workspace/agent-browser.json}"
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
export AGENT_BROWSER_SESSION_NAME="${AGENT_BROWSER_SESSION_NAME:-workspace}"
export AGENT_BROWSER_SESSION="${AGENT_BROWSER_SESSION:-workspace}"
export XDG_RUNTIME_DIR="$RUNTIME_DIR"

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
  "sessionName": "${AGENT_BROWSER_SESSION_NAME:-workspace}",
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

# ── Per-user /workspace persistence (best-effort; never blocks boot) ──────────
# The backend injects WORKSPACE_SYNC_URL + WORKSPACE_SYNC_SAS scoped to this
# user's object-storage directory. /workspace stays on the local container FS
# (fast, native inotify for Vite); we mirror it to storage: restore on boot,
# then a background push loop + a final push on SIGTERM. azcopy failures only
# cost persistence, never the sandbox — so nothing here runs under `set -e`.
export AZCOPY_LOG_LOCATION="${AZCOPY_LOG_LOCATION:-/tmp/azcopy}"
export AZCOPY_JOB_PLAN_LOCATION="${AZCOPY_JOB_PLAN_LOCATION:-/tmp/azcopy}"
WORKSPACE_SYNC_INTERVAL="${WORKSPACE_SYNC_INTERVAL:-20}"
WORKSPACE_SYNC_EXCLUDE_PATHS="${WORKSPACE_SYNC_EXCLUDE_PATHS:-node_modules;dist;.vite;coverage;.vitest-attachments;.browser-profile;.cache}"
WORKSPACE_SYNC_EXCLUDE_PATTERN="${WORKSPACE_SYNC_EXCLUDE_PATTERN:-*.tsbuildinfo}"
_RESTORE_OK=0

workspace_persist_enabled() {
  [ -n "${WORKSPACE_SYNC_URL:-}" ] && [ -n "${WORKSPACE_SYNC_SAS:-}" ] && command -v azcopy >/dev/null 2>&1
}

workspace_restore() {
  echo "workspace: restoring /workspace from remote..." >&2
  if azcopy sync "${WORKSPACE_SYNC_URL}?${WORKSPACE_SYNC_SAS}" "/workspace" \
      --delete-destination=false >/tmp/azcopy-restore.log 2>&1; then
    _RESTORE_OK=1
    echo "workspace: restore complete" >&2
  else
    echo "workspace: restore FAILED — starting from current state (see /tmp/azcopy-restore.log)" >&2
  fi
}

workspace_save() {
  workspace_persist_enabled || return 0
  # Only let the push delete remote files once we've successfully restored, so a
  # boot that failed to restore never wipes the user's saved workspace.
  local del=false
  [ "$_RESTORE_OK" = "1" ] && del=true
  azcopy sync "/workspace" "${WORKSPACE_SYNC_URL}?${WORKSPACE_SYNC_SAS}" \
    --delete-destination="$del" \
    --exclude-path="$WORKSPACE_SYNC_EXCLUDE_PATHS" \
    --exclude-pattern="$WORKSPACE_SYNC_EXCLUDE_PATTERN" \
    >/tmp/azcopy-save.log 2>&1 || echo "workspace: save failed (see /tmp/azcopy-save.log)" >&2
}

if workspace_persist_enabled; then
  workspace_restore
  ( while sleep "$WORKSPACE_SYNC_INTERVAL"; do workspace_save; done ) &
  _SYNC_LOOP_PID=$!

  term_handler() {
    echo "workspace: SIGTERM — final save" >&2
    workspace_save
    [ -n "${_SYNC_LOOP_PID:-}" ] && kill "$_SYNC_LOOP_PID" 2>/dev/null || true
    [ -n "${_RUNTIME_PID:-}" ] && kill -TERM "$_RUNTIME_PID" 2>/dev/null || true
  }
  trap term_handler TERM INT

  # Run the runtime server as a child (not exec) so the trap can fire a final
  # save on pod teardown. tini (PID 1) forwards SIGTERM here.
  python -m agentbox.runtime_server &
  _RUNTIME_PID=$!
  wait "$_RUNTIME_PID" || true
else
  if [ -n "${WORKSPACE_SYNC_URL:-}" ]; then
    echo "workspace: WORKSPACE_SYNC_URL set but azcopy/SAS unavailable — persistence disabled" >&2
  fi
  exec python -m agentbox.runtime_server
fi
