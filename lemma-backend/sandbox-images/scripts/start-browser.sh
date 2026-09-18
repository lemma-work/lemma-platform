#!/usr/bin/env bash
set -euo pipefail

DISPLAY_VALUE="${DISPLAY:-:99}"
# The framebuffer Xvfb allocates. It is both the size the display starts at
# and the ceiling on every later resize: RandR can pick a smaller mode out of a
# big framebuffer but cannot grow one, so a viewer asking for a display that
# matches their pane (`/display:resize`) is bounded by this. 1920x1200x24 is
# ~9 MB.
#
# The ceiling and the size actually shown are two different things, and both
# matter. Allocating 1920x1200 is what lets a wide pane be matched later;
# *running* at it costs every frame x11vnc encodes and every frame `record`
# grabs ~1.67x what 1440x960 does, for a picture nobody asked to be that big.
# Left at the ceiling, that was enough to kill a screen recording mid-take on
# a 2 GB sandbox on a loaded CI runner -- the recording started, died, and
# `record stop` reported "No recording in progress".
#
# So: allocate the ceiling, then size the mode down to the default before any
# X client is started. The earlier attempt at this *was* a race -- the same
# command landed sometimes and not others -- because x11vnc was already up and
# grabbing its first frame while the mode changed underneath it. Done here it
# cannot be: x11vnc and the window manager are started further down, and the
# branch that restarts Xvfb kills both first.
SCREEN="${WORKSPACE_XVFB_MAX_SCREEN:-${WORKSPACE_XVFB_SCREEN:-1920x1200x24}}"
START_SCREEN="${WORKSPACE_XVFB_SCREEN:-1440x960x24}"
PROFILE_DIR="${AGENT_BROWSER_PROFILE:-/home/user/.lemma/browser/profile}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/lemma-browser/runtime}"
CONFIG_PATH="${AGENT_BROWSER_CONFIG:-/tmp/lemma-browser/config.json}"
EXECUTABLE_PATH="${AGENT_BROWSER_EXECUTABLE_PATH:-/usr/local/bin/workspace-chrome}"
DISPLAY_NUMBER="${DISPLAY_VALUE#:}"
DISPLAY_NUMBER="${DISPLAY_NUMBER%%.*}"
HOME_DIR="${HOME:-/home/user}"
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
# `--test-type` is here for one reason: it suppresses the yellow "You are
# using an unsupported command-line flag: --no-sandbox" infobar. That bar is
# not a warning anybody in this product can act on -- the sandbox flag is
# required to run Chrome inside a container, and the container *is* the
# isolation boundary -- but it sits across the top of every frame the person
# watching sees, steals a strip of the page, and reads like the browser is
# broken. It changes no behaviour beyond hiding infobars and a first-run
# bubble; it does not make this a "test build" of Chrome, which is a
# different thing -- the binary is whatever `workspace-chrome` points at,
# Debian Chromium on the Docker image and `google-chrome-stable` on E2B.
CHROME_ARGS="--no-sandbox,--test-type,--disable-dev-shm-usage,--no-first-run,--no-default-browser-check,--disable-blink-features=AutomationControlled"
# Chrome's own size is *not* set here, and cannot be: this list is
# comma-separated (agent-browser splits it), and every flag that would say a
# size takes a comma inside its value. `--window-size=1920,1200` arrives at
# Chrome as `--window-size=1920` followed by a stray `1200`, which Chrome
# ignores -- so the window stayed at its default 945px while the flag looked
# present in the config. What fills the display is the window manager started
# further down, which maximises whatever Chrome opens; that is measured, and it
# keeps working when a viewer resizes the display underneath it.
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
  # Take the old display's clients with it.
  #
  # x11vnc and the window manager outlive the X server they were attached to --
  # measured, by killing Xvfb underneath them and finding both still running.
  # The `pgrep` guards below then see a live process and skip starting one, so
  # the viewer is served by an x11vnc bound to a display that no longer exists:
  # a framebuffer that never updates, which reaches a person as a pane that
  # connects and then shows nothing. Exactly what the shared-display e2e tests
  # caught, because they kill Xvfb between cases and the next case inherits the
  # wreckage.
  #
  # Only in this branch: if Xvfb is still up, its clients are attached to the
  # server we are keeping and restarting them would be the bug rather than the
  # fix.
  pkill -f "x11vnc .*-rfbport ${LEMMA_BROWSER_VNC_PORT:-5900}" >/dev/null 2>&1 || true
  pkill -f "matchbox-window-manager -display ${DISPLAY_VALUE}" >/dev/null 2>&1 || true
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

# The socket existing is not the server answering, and everything below this
# line is an X client.
#
# Xvfb binds its socket before it will accept a connection, and how much
# before depends on the fabric: on Docker the gap is invisible, on E2B it is
# long enough that the window manager came up with "can't open display" and
# the initial resize silently did nothing -- so the browser filled neither the
# screen nor the pane, on the one fabric that serves real users. Waited for
# once, here, rather than left for each client to discover.
if command -v xrandr >/dev/null 2>&1; then
  waited=0
  while [ "$waited" -lt 200 ] \
    && ! DISPLAY="$DISPLAY_VALUE" xrandr --current >/dev/null 2>&1; do
    sleep 0.05
    waited=$((waited + 1))
  done
fi

# Somebody has to manage the windows, or nobody does, and without one Chrome
# sits at its default 945px in the corner of the screen while an OAuth popup --
# which almost every real sign-in opens -- is placed wherever the X server
# likes, possibly off-screen, where the person watching sees nothing happen.
# matchbox is the smallest thing that fixes both: one window at a time,
# maximised, no decoration to click on by accident. It is also what makes a
# `/display:resize` visible, because it re-maximises Chrome onto the new size.
#
# Started, then *checked*. A background launch that loses its race says nothing
# and leaves no log -- observed, with an empty `lemma-wm.log` and no process --
# and the cost of not noticing is the whole point of having it. Two attempts,
# because the failure it is guarding against is a startup race rather than a
# misconfiguration, and a second one either works or is telling us something
# else is wrong.
if command -v matchbox-window-manager >/dev/null 2>&1; then
  attempt=0
  while [ "$attempt" -lt 2 ] \
    && ! pgrep -f "matchbox-window-manager -display ${DISPLAY_VALUE}" >/dev/null 2>&1; do
    setsid nohup matchbox-window-manager -display "$DISPLAY_VALUE" -use_titlebar no \
      >/tmp/lemma-wm.log 2>&1 < /dev/null &
    waited=0
    while [ "$waited" -lt 20 ] \
      && ! pgrep -f "matchbox-window-manager -display ${DISPLAY_VALUE}" >/dev/null 2>&1; do
      sleep 0.05
      waited=$((waited + 1))
    done
    attempt=$((attempt + 1))
  done
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

# Down to the size we actually mean to run at.
#
# Here, and not earlier, because `set-display-size` documents why: against an
# Xvfb that x11vnc has not attached to, `xrandr --newmode` exits 0 and creates
# nothing, so a size-down before this point silently does not happen -- which
# is exactly what it did when it was placed above, and why an earlier attempt
# at this was abandoned as "racy".
#
# Worth doing rather than living at the ceiling: the framebuffer is allocated
# at `SCREEN` so a wide pane can still be matched later, but *running* at
# 1920x1200 makes every frame x11vnc encodes and every frame `agent-browser
# record` grabs ~1.67x the work of 1440x960. On a 2 GB sandbox that was enough
# to lose a screen recording part-way through, with `record stop` reporting
# "No recording in progress" and nothing saying why.
#
# Non-fatal: a display left at the ceiling is a bigger picture than intended,
# which is worth a line in the log and not a failed browser.
#
# One more ordering constraint, learned the hard way twice: x11vnc must be
# *settled*, not merely started. A mode change that lands while it is taking
# its first frame kills it outright -- `X_GetImage`, and the pane then has
# nothing to connect to. Once it is serving, it follows a resize happily
# (`-xrandr resize`), which is why every later `/display:resize` is safe.
# So: wait for the port to answer, then a breath, then change the mode.
if [ "$START_SCREEN" != "$SCREEN" ] && command -v set-display-size >/dev/null 2>&1; then
  waited=0
  while [ "$waited" -lt 100 ] \
    && ! (exec 3<>"/dev/tcp/127.0.0.1/${VNC_PORT}") 2>/dev/null; do
    sleep 0.05
    waited=$((waited + 1))
  done
  sleep 0.5
  start_w="${START_SCREEN%%x*}"
  start_rest="${START_SCREEN#*x}"
  start_h="${start_rest%%x*}"
  if ! DISPLAY="$DISPLAY_VALUE" set-display-size "$start_w" "$start_h" \
    >/tmp/lemma-initial-size.log 2>&1; then
    echo "[start-browser] could not size the display to ${start_w}x${start_h}" >&2
  fi
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
