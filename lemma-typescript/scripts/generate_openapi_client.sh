#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="$SCRIPT_DIR/.."
SPEC_TMP="$SDK_DIR/.generated/openapi.json"
CLIENT_SPEC_TMP="$SDK_DIR/.generated/openapi.client.json"
OUT_DIR="$SDK_DIR/src/openapi_client"
REPO_ROOT="$(cd "$SDK_DIR/.." && pwd)"

normalize_json_file() {
  local json_path="$1"
  "$PYTHON_BIN" - "$json_path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

# Derive the OpenAPI URL from LEMMA_API_URL if set:
#   LEMMA_API_URL=https://api.lemma.work bash generate_openapi_client.sh
# Or explicitly:
#   OPENAPI_URL=https://api.lemma.work/openapi.json bash generate_openapi_client.sh
# Or from a checked-in/local spec:
#   OPENAPI_FILE=../lemma-python/lemma_sdk/openapi_spec.json bash generate_openapi_client.sh
if [[ -n "${LEMMA_API_URL:-}" ]]; then
  OPENAPI_URL="${OPENAPI_URL:-${LEMMA_API_URL%/}/openapi.json}"
fi
OPENAPI_URL="${OPENAPI_URL:-https://api.lemma.work/openapi.json}"

CURL_ARGS=()
if [[ "${OPENAPI_INSECURE:-0}" == "1" || "${LEMMA_SSL_NO_VERIFY:-0}" == "1" ]]; then
  CURL_ARGS+=("-k")
fi

mkdir -p "$SDK_DIR/.generated"
PYTHON_BIN="python"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

if [[ -n "${OPENAPI_FILE:-}" ]]; then
  cp "$OPENAPI_FILE" "$SPEC_TMP"
  echo "Loaded OpenAPI spec from $OPENAPI_FILE"
elif [[ ${#CURL_ARGS[@]} -gt 0 ]]; then
  curl "${CURL_ARGS[@]}" -fsS "$OPENAPI_URL" -o "$SPEC_TMP"
else
  curl -fsS "$OPENAPI_URL" -o "$SPEC_TMP"
fi
if [[ -z "${OPENAPI_FILE:-}" ]]; then
  echo "Fetched OpenAPI spec from $OPENAPI_URL"
fi

normalize_json_file "$SPEC_TMP"
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_client_openapi.py" \
  "$SPEC_TMP" \
  "$CLIENT_SPEC_TMP" \
  --exclude-tag billing-subscriptions \
  --exclude-tag billing-webhooks \
  --exclude-tag scheduler \
  --exclude-tag webhooks \
  --exclude-prefix /billing \
  --exclude-prefix /scheduler \
  --exclude-prefix /webhooks
normalize_json_file "$CLIENT_SPEC_TMP"

cd "$SDK_DIR"
# Pin the generator for deterministic, drift-gate-friendly output. Prefer the
# locally-installed devDependency (openapi-typescript-codegen, pinned in
# package.json); fall back to a version-pinned npx so CI without node_modules
# still produces the same bytes.
GENERATOR_BIN="$SDK_DIR/node_modules/.bin/openapi"
GENERATOR_VERSION="0.29.0"
if [[ -x "$GENERATOR_BIN" ]]; then
  "$GENERATOR_BIN" \
    --input "$CLIENT_SPEC_TMP" \
    --output "$OUT_DIR" \
    --client fetch
else
  npx --yes "openapi-typescript-codegen@${GENERATOR_VERSION}" \
    --input "$CLIENT_SPEC_TMP" \
    --output "$OUT_DIR" \
    --client fetch
fi

node "$SDK_DIR/scripts/patch_generated_imports.mjs" "$OUT_DIR"

echo "Generated compatibility TypeScript client in src/openapi_client"
