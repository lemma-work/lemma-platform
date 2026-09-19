#!/usr/bin/env bash
# The viewing half of the display stack, brought up when somebody looks.
#
# x11vnc and websockify exist for a person watching the sandbox, and most of
# the time nobody is: an agent doing research holds the display, the browser
# and the relay and would pay for these two as well. Measured by starting one
# process at a time in a 2 GB sandbox -- Xvfb 21 MiB, matchbox 9, x11vnc 27,
# websockify 40 -- so deferring them is 66 MiB, the whole viewing chain, kept
# back until a socket actually wants a picture.
#
# Idempotent by `pgrep`, like `start-browser-relay`, because every viewer
# arrival runs this and most of them find it already up. Safe to run
# concurrently: the losers of the race find the port bound and return.
#
# Not merged into `lemma-ensure-display`: that script is what the *agent's*
# paths call, and the whole point is that they do not pay for this.
set -euo pipefail

DISPLAY_VALUE="${DISPLAY:-:99}"
VNC_PORT="${LEMMA_BROWSER_VNC_PORT:-5900}"
VNC_WS_PORT="${LEMMA_BROWSER_VNC_WS_PORT:-5901}"

if ! pgrep -f "x11vnc .*-rfbport ${VNC_PORT}" >/dev/null 2>&1; then
  # `-noshm`: MIT-SHM attach fails under this container's X server and takes
  # x11vnc down with it moments after a clean-looking start -- proven by
  # running it without the flag, not assumed. `setsid`, so it outlives the
  # request that started it.
  # `-xrandr resize`: follow the display when it changes size instead of
  # serving the geometry it saw at startup. Without it a `/display:resize`
  # leaves x11vnc describing a screen that no longer exists, and the viewer
  # gets a picture that does not match the framebuffer behind it.
  setsid nohup x11vnc -display "$DISPLAY_VALUE" -noshm -forever -shared -nopw \
    -rfbport "$VNC_PORT" -listen 127.0.0.1 -noxdamage -quiet -xrandr resize \
    >/tmp/lemma-x11vnc.log 2>&1 < /dev/null &
fi

# Waited for before websockify starts. websockify dials 5900 per incoming
# connection, so it tolerates a late x11vnc -- but a viewer that arrives in
# the gap gets a refused upstream and reads it as "the browser is gone".
waited=0
while [ "$waited" -lt 100 ] \
  && ! (exec 3<>"/dev/tcp/127.0.0.1/${VNC_PORT}") 2>/dev/null; do
  sleep 0.05
  waited=$((waited + 1))
done

if ! pgrep -f "websockify .*${VNC_WS_PORT}" >/dev/null 2>&1; then
  setsid nohup websockify --heartbeat 30 127.0.0.1:"$VNC_WS_PORT" 127.0.0.1:"$VNC_PORT" \
    >/tmp/lemma-websockify.log 2>&1 < /dev/null &
fi

# Exit only once the far end answers, so the caller can treat a zero exit as
# "a viewer would get a picture" rather than "two processes were spawned".
waited=0
while [ "$waited" -lt 100 ] \
  && ! (exec 3<>"/dev/tcp/127.0.0.1/${VNC_WS_PORT}") 2>/dev/null; do
  sleep 0.05
  waited=$((waited + 1))
done
if ! (exec 3<>"/dev/tcp/127.0.0.1/${VNC_WS_PORT}") 2>/dev/null; then
  echo "start-vnc-bridge: nothing is listening on ${VNC_WS_PORT}" >&2
  exit 1
fi
