#!/usr/bin/env bash
set -euo pipefail

DISPLAY_VALUE="${DISPLAY:-:99}"
SCREEN="${WORKSPACE_XVFB_SCREEN:-1440x960x24}"
# The framebuffer Xvfb allocates, which is the ceiling on every later resize:
# RandR can pick a smaller mode out of a big framebuffer but cannot grow one.
# A viewer asking for a display that matches their pane (`/display:resize`) is
# therefore bounded by this, not by `WORKSPACE_XVFB_SCREEN`, which is only the
# size we start at. 1920x1200x24 is ~9 MB of framebuffer.
MAX_SCREEN="${WORKSPACE_XVFB_MAX_SCREEN:-1920x1200x24}"
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
# Regenerated every run rather than only when missing. A resumed sandbox
# keeps whatever `config.json` its last run wrote, and a proxy assigned since
# then -- a new resume can land on a different sandbox instance -- must reach
# Chrome's next launch, not wait for a profile that happens not to exist yet.
CHROME_ARGS="--no-sandbox,--disable-dev-shm-usage,--no-first-run,--no-default-browser-check,--disable-blink-features=AutomationControlled"
# Fill the display rather than sitting in the corner of it. Chrome opens at its
# own default size -- measured at 1050x940 inside a 1440x960 screen, so 27% of
# every streamed frame was empty desktop before any scaling. There is no window
# manager to maximise it and no person to drag it, so the size is given here.
# `--window-position=0,0` because a window the same size as the screen placed
# anywhere else is worse than one that is merely too small.
SCREEN_WIDTH="${SCREEN%%x*}"
SCREEN_REST="${SCREEN#*x}"
SCREEN_HEIGHT="${SCREEN_REST%%x*}"
CHROME_ARGS="${CHROME_ARGS},--window-size=${SCREEN_WIDTH},${SCREEN_HEIGHT},--window-position=0,0"
if [ -n "${AGENT_BROWSER_PROXY:-}" ]; then
  # `AGENT_BROWSER_PROXY` (comma-separated below is Chrome's own args syntax,
  # not this one -- agent-browser reads that env var itself) can carry inline
  # `user:pass@host:port`: agent-browser parses the credentials out before
  # ever putting the server on Chrome's command line and answers Chrome's CDP
  # `Fetch.authRequired` event with them, so a credentialed proxy works with
  # no special handling here. The WebRTC flag still needs adding ourselves --
  # without it, the sandbox's real IP is visible to any page in ICE
  # candidates gathered outside the proxy, which defeats the point of having
  # one.
  CHROME_ARGS="${CHROME_ARGS},--force-webrtc-ip-handling-policy=disable_non_proxied_udp"
fi
# The per-sandbox extension point this file's comment has always promised and
# never implemented: `AGENT_BROWSER_ARGS` was named here as the way to add a
# Chrome flag without editing the image, and nothing read it. Appended last so
# a sandbox can override anything above it.
if [ -n "${AGENT_BROWSER_ARGS:-}" ]; then
  CHROME_ARGS="${CHROME_ARGS},${AGENT_BROWSER_ARGS}"
fi
mkdir -p "$(dirname "$CONFIG_PATH")"
cat > "$CONFIG_PATH" <<EOF
{
  "headed": true,
  "profile": "$PROFILE_DIR",
  "executablePath": "$EXECUTABLE_PATH",
  "args": "$CHROME_ARGS"
}
EOF
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
  # Started at the *maximum* size, then sized down to `SCREEN` below. RandR
  # cannot grow a framebuffer past the one allocated at startup, so a display
  # started at 1440x960 could never be resized to a wider pane -- and matching
  # the viewer's shape is the whole point of `/display:resize`.
  setsid nohup Xvfb "$DISPLAY_VALUE" -screen 0 "$MAX_SCREEN" -ac +extension RANDR \
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

# Somebody has to manage the windows, or nobody does. Chrome is told its size
# above, but a *second* window -- the OAuth popup almost every real sign-in
# opens -- is placed by the X server's default, which is wherever it likes and
# possibly off-screen. A person watching a sign-in then sees nothing happen.
# matchbox is the smallest thing that fixes it: one window at a time, filling
# the screen, no decoration to click on by accident.
if command -v matchbox-window-manager >/dev/null 2>&1; then
  if ! pgrep -f "matchbox-window-manager" >/dev/null 2>&1; then
    setsid nohup matchbox-window-manager -display "$DISPLAY_VALUE" -use_titlebar no \
      >/tmp/lemma-wm.log 2>&1 < /dev/null &
  fi
fi

# The human-facing view of this same display, over VNC rather than the
# stream server's JPEG frames -- a real clipboard and no coordinate space to
# get wrong, at the cost of showing the whole display rather than one tab.
# Both processes are loopback-only; nothing outside the relay ever dials
# either port. Idempotent by pgrep for the same reason as Xvfb above: this
# script runs from every `exec_command` that wants a browser, not once per
# sandbox.
VNC_PORT="${LEMMA_BROWSER_VNC_PORT:-5900}"
VNC_WS_PORT="${LEMMA_BROWSER_VNC_WS_PORT:-5901}"
if ! pgrep -f "x11vnc .*-rfbport ${VNC_PORT}" >/dev/null 2>&1; then
  # `-noshm`: MIT-SHM attach fails under this container's X server and takes
  # x11vnc down with it moments after a clean-looking start -- proven by
  # running it without the flag, not assumed. `setsid`, same reason as Xvfb.
  # `-xrandr resize`: follow the display when it changes size instead of
  # serving the geometry it saw at startup. Without it a `/display:resize`
  # leaves x11vnc describing a screen that no longer exists, and the viewer
  # gets a picture that does not match the framebuffer behind it.
  setsid nohup x11vnc -display "$DISPLAY_VALUE" -noshm -forever -shared -nopw \
    -rfbport "$VNC_PORT" -listen 127.0.0.1 -noxdamage -quiet -xrandr resize \
    >/tmp/lemma-x11vnc.log 2>&1 < /dev/null &
fi
if ! pgrep -f "websockify .*${VNC_WS_PORT}" >/dev/null 2>&1; then
  setsid nohup websockify --heartbeat 30 127.0.0.1:"$VNC_WS_PORT" 127.0.0.1:"$VNC_PORT" \
    >/tmp/lemma-websockify.log 2>&1 < /dev/null &
fi

# Down from the framebuffer's maximum to the size we actually start at, now
# that the display stack is up.
#
# The order matters and is not obvious: `xrandr --newmode` against a bare Xvfb
# returns success and silently creates nothing, so this ran as a no-op when it
# sat next to the Xvfb start and the display stayed at its full 1920x1200.
# With x11vnc attached the same call works -- proven by running it either side
# of starting x11vnc on one display, not reasoned about. Best effort either
# way: a display left at the maximum is a picture that is too big, which is a
# far smaller problem than refusing to start a browser over it.
if command -v set-display-size >/dev/null 2>&1; then
  DISPLAY="$DISPLAY_VALUE" set-display-size "$SCREEN_WIDTH" "$SCREEN_HEIGHT" \
    >/dev/null 2>&1 || true
fi

# The relay is what the backend reaches, and now the only way in.
#
# `agent-browser dashboard`, republished on 0.0.0.0:4848 by a socat forwarder,
# used to start here too. VNC replaced it as the human view, and what it left
# behind was a second, unauthenticated door into the same browser -- one with a
# Storage panel listing the session's cookies and a console that evaluates
# script. `browser_view_service._require_private` exists largely to refuse a
# sandbox where that door is reachable; removing the door is the better half of
# that fix, and it gives a 2 GB sandbox two processes back.
start-browser-relay || true

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
