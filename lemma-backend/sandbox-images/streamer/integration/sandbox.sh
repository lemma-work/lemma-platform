#!/usr/bin/env bash
# Display, window manager, Chrome on the probe page, and the stream key.
set -eu
export DISPLAY=:99
mkdir -p /tmp/lemma-relay /tmp/lemma-browser
printf '{"key":"%s","machine":"%s"}' "$STREAM_KEY_HEX" "$MACHINE_ID" > /tmp/lemma-relay/stream.json
Xvfb :99 -screen 0 "${SCREEN:-1280x800x24}" -nolisten tcp >/tmp/xvfb.log 2>&1 &
for _ in $(seq 100); do xdpyinfo >/dev/null 2>&1 && break; [ -e /tmp/.X11-unix/X99 ] && break; sleep 0.05; done
sleep 0.3
matchbox-window-manager -use_titlebar no >/dev/null 2>&1 &
chromium --no-sandbox --no-first-run --disable-gpu --kiosk --window-size="${WINDOW:-1280,800}" \
  --force-device-scale-factor="${DPR:-1}" --user-data-dir=/tmp/chrome "file:///it/probe.html${PAGE_HASH:-}" >/tmp/chrome.log 2>&1 &
# Signalling reachable from other containers, for the TURN check only.
socat TCP-LISTEN:4861,fork,reuseaddr TCP:127.0.0.1:4860 >/dev/null 2>&1 &
exec sleep infinity
