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
if [[ -z "${OPENAPI_URL:-}" && -z "${OPENAPI_FILE:-}" ]]; then
  OPENAPI_URL="https://api.lemma.work/openapi.json"
  OPENAPI_USED_PROD_DEFAULT=1
fi
OPENAPI_URL="${OPENAPI_URL:-https://api.lemma.work/openapi.json}"

if [[ "${OPENAPI_USED_PROD_DEFAULT:-0}" == "1" ]]; then
  cat >&2 <<'WARN'

  !! Regenerating from PRODUCTION (https://api.lemma.work/openapi.json).
     This does NOT reflect route changes in your working tree, and production
     can trail main -- so routes that exist on main may be DELETED from the
     generated client. That has happened.

     Working on backend routes? Generate from your own tree instead:

       cd lemma-backend && uv run python scripts/dump_openapi_spec.py \
         --output ../lemma-python/lemma_sdk/openapi_spec.json
       cd ../lemma-typescript && \
         OPENAPI_FILE=../lemma-python/lemma_sdk/openapi_spec.json \
         bash scripts/generate_openapi_client.sh

     Releasing the SDK against deployed prod? Then this default is correct.

WARN
fi

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
  "$CLIENT_SPEC_TMP"
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

# Keep the checked-in spec beside the client it produced. This file is tracked
# but nothing wrote it, so it drifted from the API silently -- by the time this
# was noticed it was missing fields the server had shipped and still carried an
# enum value the server no longer had. Writing it here means it cannot drift
# again without the client drifting with it.
cp "$SPEC_TMP" "$SDK_DIR/src/openapi_spec.json"

echo "Generated compatibility TypeScript client in src/openapi_client"
