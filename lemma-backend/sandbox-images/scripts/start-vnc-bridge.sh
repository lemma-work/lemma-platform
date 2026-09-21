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
# Idempotent, because every viewer arrival runs this and most of them find
# it already up -- and idempotent **by the port**, with `pgrep -x` demoted
# to a secondary guard against stacking. It was
# `pgrep -f "websockify .*$PORT"`, and `-f` matches any process whose whole
# command line contains both words, including the shell that invoked this
# script. Measured: with the port in the caller's own command line, that
# guard matched PIDs 1, 6 and the caller, concluded websockify was already
# up, started nothing, and then sat out the entire budget waiting for a
# process that did not exist -- reported as a timeout, which is what sent
# three attempts at this chasing start-up latency. `-x` matches the program
# name, which no shell can accidentally be.
#
# Safe to run concurrently: the losers of the race find the port bound and
# return.
#
# Not merged into `lemma-ensure-display`: that script is what the *agent's*
# paths call, and the whole point is that they do not pay for this.
set -euo pipefail

#: Two budgets, because the two processes are not alike. x11vnc is a C
#: binary, measured binding in 55-117 ms; websockify is a Python program
#: whose cold start pays for an interpreter and its imports, measured at
#: 57-158 ms. Both numbers come from an idle machine with a warm page
#: cache, and CI is four shared vCPUs with several containers on them, so
#: neither is an upper bound on anything.
#:
#: Thirty seconds is therefore deliberately far more than any measurement
#: justifies. It is not a fix for a known slowness -- the self-match above
#: was the failure, and these loops return the moment the port answers, so
#: a healthy cold start pays 166 ms and a warm one 2 ms. The number only
#: has to be large enough that a slow start is never mistaken for a broken
#: one again, and nobody waits it out except a sandbox that is genuinely
#: failing.
X11VNC_WAIT_SECONDS=5
WEBSOCKIFY_WAIT_SECONDS=30
TICK=0.05

DISPLAY_VALUE="${DISPLAY:-:99}"
VNC_PORT="${LEMMA_BROWSER_VNC_PORT:-5900}"
VNC_WS_PORT="${LEMMA_BROWSER_VNC_WS_PORT:-5901}"

X11VNC_LOG=/tmp/lemma-x11vnc.log
WEBSOCKIFY_LOG=/tmp/lemma-websockify.log

# Answers "would a viewer get through to this port", which is the only
# question worth asking: a process being up says nothing, and both of these
# are up for a while before they listen.
port_answers() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

await_port() {
  local port="$1" seconds="$2" ticks
  ticks=$(awk -v s="$seconds" -v t="$TICK" 'BEGIN { printf "%d", s / t }')
  local waited=0
  while [ "$waited" -lt "$ticks" ] && ! port_answers "$port"; do
    sleep "$TICK"
    waited=$((waited + 1))
  done
  port_answers "$port"
}

# Said out loud rather than left to the caller, which sees an exit code and a
# one-line message. The last CI failure here reported only "nothing is
# listening on 5901" and cost a day of guessing at causes that the log on
# disk would have named.
explain() {
  local what="$1" log="$2" program="$3"
  echo "start-vnc-bridge: $what" >&2
  if pgrep -x "$program" >/dev/null 2>&1; then
    echo "--- $program is running but not listening ---" >&2
    pgrep -a -x "$program" >&2 || true
  else
    echo "--- no $program process exists: it never started, or it died ---" >&2
  fi
  if [ -s "$log" ]; then
    echo "--- last lines of $log ---" >&2
    tail -n 15 "$log" >&2
  else
    echo "--- $log is empty or absent: the process never wrote anything ---" >&2
  fi
}

# The fast path, and the common one: a warm sandbox with a second viewer
# arriving does no work at all and spawns no `pgrep`.
if port_answers "$VNC_PORT" && port_answers "$VNC_WS_PORT"; then
  exit 0
fi

if ! port_answers "$VNC_PORT" && ! pgrep -x x11vnc >/dev/null 2>&1; then
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
    >"$X11VNC_LOG" 2>&1 < /dev/null &
fi

# Waited for before websockify starts, and refused here rather than after:
# websockify will happily bind its own port with no upstream behind it, so a
# final probe alone would pass while the first viewer gets a connection
# failure from 5900 and reads it as "the browser is gone".
if ! await_port "$VNC_PORT" "$X11VNC_WAIT_SECONDS"; then
  explain "x11vnc is not listening on ${VNC_PORT}" "$X11VNC_LOG" x11vnc
  exit 1
fi

if ! port_answers "$VNC_WS_PORT" && ! pgrep -x websockify >/dev/null 2>&1; then
  setsid nohup websockify --heartbeat 30 127.0.0.1:"$VNC_WS_PORT" 127.0.0.1:"$VNC_PORT" \
    >"$WEBSOCKIFY_LOG" 2>&1 < /dev/null &
fi

# Exit only once the far end answers, so the caller can treat a zero exit as
# "a viewer would get a picture" rather than "two processes were spawned".
if ! await_port "$VNC_WS_PORT" "$WEBSOCKIFY_WAIT_SECONDS"; then
  explain "nothing is listening on ${VNC_WS_PORT}" "$WEBSOCKIFY_LOG" websockify
  exit 1
fi
