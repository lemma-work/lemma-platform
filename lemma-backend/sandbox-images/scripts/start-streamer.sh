#!/usr/bin/env bash
# The WebRTC half of the viewing stack, brought up when somebody looks.
#
# `lemma-streamer` captures the display, encodes it once for every viewer and
# answers signalling on a loopback port the relay dials. Like the VNC bridge
# it is started by the first viewer rather than with the display: most of the
# time nobody is watching, and the agent should not pay for a streamer then.
# It exits on its own after two minutes with nobody watching.
#
# Idempotent by the port, as `start-vnc-bridge` is and for the reason written
# there: `pgrep -f` matches the shell that called this, so the port is the
# test and `pgrep -x` only a guard against stacking a second process.
#
# Exit codes: 0 the streamer answers; 1 it did not come up (the log is
# printed); 3 this image has no streamer at all -- an image built before it
# existed, where the viewer should use VNC instead.
set -euo pipefail

STREAMER_WAIT_SECONDS=10
TICK=0.05

STREAM_PORT="${LEMMA_BROWSER_STREAM_PORT:-4860}"
STREAMER_LOG=/tmp/lemma-streamer.log

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

explain() {
  echo "start-streamer: $1" >&2
  if pgrep -x lemma-streamer >/dev/null 2>&1; then
    echo "--- lemma-streamer is running but not listening ---" >&2
  else
    echo "--- no lemma-streamer process exists: it never started, or it died ---" >&2
  fi
  if [ -s "$STREAMER_LOG" ]; then
    echo "--- last lines of $STREAMER_LOG ---" >&2
    tail -n 15 "$STREAMER_LOG" >&2
  else
    echo "--- $STREAMER_LOG is empty or absent ---" >&2
  fi
}

if port_answers "$STREAM_PORT"; then
  exit 0
fi

if ! command -v lemma-streamer >/dev/null 2>&1; then
  echo "start-streamer: this machine's image has no streamer (lemma-streamer is not installed)" >&2
  exit 3
fi

if ! pgrep -x lemma-streamer >/dev/null 2>&1; then
  # `setsid`, so it outlives the request that started it.
  DISPLAY="${DISPLAY:-:99}" LEMMA_BROWSER_STREAM_PORT="$STREAM_PORT" \
    setsid nohup lemma-streamer >"$STREAMER_LOG" 2>&1 </dev/null &
fi

if ! await_port "$STREAM_PORT" "$STREAMER_WAIT_SECONDS"; then
  explain "nothing is listening on ${STREAM_PORT}"
  exit 1
fi
