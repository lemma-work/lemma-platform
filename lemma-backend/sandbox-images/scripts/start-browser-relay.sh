#!/usr/bin/env bash
# Start the browser relay, unless it is already up.
#
# Started from `start-browser` rather than as the image's CMD, because it is a
# companion to the browser and not to the sandbox: a workspace that never opens
# a page has no use for it, and starting it eagerly would cost memory in a
# container where 220 MB free already triggers a browser kill.
#
# Idempotent by pgrep, so calling `start-browser` a second time -- which every
# browser tool does -- does not stack processes.
set -euo pipefail

PORT="${LEMMA_BROWSER_RELAY_PORT:-4850}"
PYTHON="${LEMMA_RELAY_PYTHON:-/opt/lemma-python/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi
[ -x "$PYTHON" ] || exit 0

if pgrep -f "sandbox_runtime.browser_relay.server" >/dev/null 2>&1; then
  exit 0
fi

mkdir -p /tmp/lemma-relay
chmod 700 /tmp/lemma-relay

# PYTHONPATH, not the interpreter's own path: /app is where the image puts the
# runtime package and it is not on sys.path for an arbitrary interpreter.
LEMMA_BROWSER_RELAY_PORT="$PORT" PYTHONPATH="/app${PYTHONPATH:+:$PYTHONPATH}" \
  nohup "$PYTHON" -m sandbox_runtime.browser_relay.server \
  >/tmp/lemma-browser-relay.log 2>&1 &
