#!/usr/bin/env bash
set -euo pipefail

token_file="${AGENTBOX_RUNTIME_TOKEN_FILE:-/run/agentbox-bootstrap/token}"
deadline=$((SECONDS + 30))
while [ ! -s "$token_file" ]; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "workspace runtime token was not delivered" >&2
    exit 1
  fi
  sleep 0.02
done

exec python -m uvicorn agentbox.workspace_runtime.server:app \
  --host 0.0.0.0 \
  --port "${AGENTBOX_WORKSPACE_RUNTIME_PORT:-8080}" \
  --no-access-log
