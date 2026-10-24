#!/usr/bin/env bash
set -euo pipefail

token_file="${LEMMA_RUNTIME_TOKEN_FILE:-/run/lemma-bootstrap/token}"
deadline=$((SECONDS + 30))
while [ ! -s "$token_file" ]; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "workspace runtime token was not delivered" >&2
    exit 1
  fi
  sleep 0.02
done

# The image's interpreter by absolute path, and isolated. Not `python` from
# `PATH`, which starts with the home's `~/.python/bin` and `~/.local/bin`; and
# `-I` so no `PYTHON*` variable, no user site and none of the home's
# site-packages (the image's `.pth` skips it under `-I`) is on `sys.path` ahead
# of the runtime's own code. Every exec of every session passes through this
# process with its token, and on Docker the home outlives the container, so
# anything planted there would otherwise be what the next container ran.
exec /opt/lemma-python/bin/python -I -m uvicorn sandbox_runtime.workspace.server:app \
  --host 0.0.0.0 \
  --port "${LEMMA_WORKSPACE_RUNTIME_PORT:-8080}" \
  --no-access-log
