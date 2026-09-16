#!/usr/bin/env bash
set -euo pipefail

DISPLAY_VALUE="${DISPLAY:-:99}"
SCREEN="${WORKSPACE_XVFB_SCREEN:-1440x960x24}"
DASHBOARD_PORT="${AGENT_BROWSER_DASHBOARD_PORT:-4848}"
DASHBOARD_INTERNAL_PORT="${AGENT_BROWSER_DASHBOARD_INTERNAL_PORT:-$((DASHBOARD_PORT + 1))}"
PROFILE_DIR="${AGENT_BROWSER_PROFILE:-/tmp/lemma-browser/profile}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/lemma-browser/runtime}"
CONFIG_PATH="${AGENT_BROWSER_CONFIG:-/tmp/lemma-browser/config.json}"
EXECUTABLE_PATH="${AGENT_BROWSER_EXECUTABLE_PATH:-/usr/local/bin/workspace-chrome}"
DISPLAY_NUMBER="${DISPLAY_VALUE#:}"
DISPLAY_NUMBER="${DISPLAY_NUMBER%%.*}"
HOME_DIR="${HOME:-/home/appuser}"
if ! mkdir -p "$HOME_DIR" 2>/dev/null || [ ! -w "$HOME_DIR" ]; then
  HOME_DIR="/tmp/lemma-home-${UID:-10001}"
  mkdir -p "$HOME_DIR"
fi

export HOME="$HOME_DIR"
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
# `--disable-blink-features=AutomationControlled` is the one that matters for
# the journey this feature exists for. Chrome otherwise sets
# `navigator.webdriver` and turns on the AutomationControlled blink feature,
# and the login pages an agent meets are exactly the pages that look. Being
# refused at a sign-in wall for wearing an automation badge is a failure with
# no upside: the person is sitting there, signing in to their own account.
#
# The rest of the list is what makes Chromium run at all in a container without
# a session bus or a large /dev/shm. `AGENT_BROWSER_ARGS` can extend this per
# sandbox without editing the image.
if [ ! -f "$CONFIG_PATH" ]; then
  mkdir -p "$(dirname "$CONFIG_PATH")"
  cat > "$CONFIG_PATH" <<EOF
{
  "headed": true,
  "profile": "$PROFILE_DIR",
  "executablePath": "$EXECUTABLE_PATH",
  "args": "--no-sandbox,--disable-dev-shm-usage,--no-first-run,--no-default-browser-check,--disable-blink-features=AutomationControlled"
}
EOF
fi
if ! mkdir -p "$RUNTIME_DIR" 2>/dev/null || [ ! -w "$RUNTIME_DIR" ]; then
  RUNTIME_DIR="/tmp/agent-browser-runtime-${UID:-10001}"
  mkdir -p "$RUNTIME_DIR"
fi
export XDG_RUNTIME_DIR="$RUNTIME_DIR"

# Ask for a live X server, not for the evidence that one used to be here.
#
# `/tmp/.X11-unix/X99` is a file in the container's writable layer, so it
# survives a restart -- and restarting the container is exactly what resuming a
# paused sandbox does. The Xvfb process does not survive. So a socket test alone
# is satisfied by a stale file, Xvfb is never started, and from then on every
# browser command in that sandbox dies with
#
#     ERROR:ui/ozone/platform/x11/ozone_platform_x11.cc: Missing X server or $DISPLAY
#     ERROR:ui/aura/env.cc: The platform failed to initialize.  Exiting.
#
# for the life of the container -- the agent's commands and the relay behind a
# person watching alike, with nothing in the sandbox to clear it. This is the
# same trap `DevToolsActivePort` sets (see browser_relay/chrome.py) and it takes
# the same answer: do not believe a file that outlives the process that made it.
if ! pgrep -f "Xvfb ${DISPLAY_VALUE} " >/dev/null 2>&1; then
  # The socket goes too, not just the lock. Xvfb gates on the lock, but a stale
  # socket left in place is what a client connects to and finds nobody behind.
  rm -f "/tmp/.X${DISPLAY_NUMBER}-lock" "/tmp/.X11-unix/X${DISPLAY_NUMBER}"
  # `setsid`, not just `nohup` -- the same reason `start-browser-relay` needs it.
  # This script is usually reached from an `exec_command` the backend makes, and
  # an exec's process group is torn down when the operation that owns it
  # finishes. `nohup` blocks SIGHUP; it does nothing about the group being
  # killed. So a merely-backgrounded Xvfb dies moments after start-browser
  # returns "done", and the *next* command in the same sandbox reports "Missing
  # X server or $DISPLAY" -- which reads like a broken image rather than like a
  # server that was killed for being in the wrong process group.
  setsid nohup Xvfb "$DISPLAY_VALUE" -screen 0 "$SCREEN" -ac +extension RANDR \
    >/tmp/lemma-xvfb.log 2>&1 < /dev/null &
  # Waited for, not slept through. Xvfb binds in ~23ms on an idle native
  # container, so 0.4s looked generous -- but it is a fixed guess either way,
  # and on a loaded or emulated machine losing that guess costs a failed browser
  # launch rather than a slower one. Five seconds is a ceiling for a hung start,
  # not a pace: the common case leaves this loop in well under a tenth of one.
  waited=0
  while [ ! -S "/tmp/.X11-unix/X${DISPLAY_NUMBER}" ] && [ "$waited" -lt 100 ]; do
    sleep 0.05
    waited=$((waited + 1))
  done
fi

# The relay is what the backend reaches; the dashboard is what a person could
# reach directly if a fabric published its port. Both are started here, and only
# the relay is ever addressed from outside.
start-browser-relay || true

agent-browser dashboard start --port "$DASHBOARD_INTERNAL_PORT" >/tmp/agent-browser-dashboard.log 2>&1 || true
if ! pgrep -f "socat.*TCP-LISTEN:${DASHBOARD_PORT}" >/dev/null 2>&1; then
  # `setsid` for the same reason as Xvfb above: started from an exec, a merely
  # backgrounded forwarder goes down with that exec's process group and the
  # published port then refuses every connection.
  setsid nohup socat TCP-LISTEN:"$DASHBOARD_PORT",fork,reuseaddr,bind=0.0.0.0 \
    TCP:127.0.0.1:"$DASHBOARD_INTERNAL_PORT" \
    >/tmp/agent-browser-dashboard-forwarder.log 2>&1 < /dev/null &
fi

if [ "$#" -gt 0 ]; then
  open_log="/tmp/agent-browser-open.log"
  if agent-browser open "$@" >"$open_log" 2>&1; then
    open_status=0
  else
    open_status=$?
  fi
  cat "$open_log"
  exit "$open_status"
fi

open_log="/tmp/agent-browser-open.log"
if agent-browser open >"$open_log" 2>&1; then
  open_status=0
else
  open_status=$?
fi
cat "$open_log"
exit "$open_status"
