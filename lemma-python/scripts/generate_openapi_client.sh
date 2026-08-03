#!/usr/bin/env bash
set -euo pipefail

# Run from repo root:
#   bash scripts/generate_openapi_client.sh
#
# Defaults:
#   OPENAPI_URL=https://api.lemma.work/openapi.json
#
# Local override examples:
#   LEMMA_API_URL=http://127.0.0.1:8000 bash scripts/generate_openapi_client.sh
#   OPENAPI_URL=http://127.0.0.1:8000/openapi.json OPENAPI_INSECURE=1 bash scripts/generate_openapi_client.sh
#   OPENAPI_FILE=lemma_sdk/openapi_spec.json bash scripts/generate_openapi_client.sh

# Pin the codegen + formatter so regeneration is deterministic. Unpinned
# `uv tool run --from <tool>` fetches the latest each run, which drifts the
# committed client and turns the CI codegen-drift gate red on tool releases.
# Bump these intentionally (and commit the regenerated client) when upgrading.
OPENAPI_PYTHON_CLIENT_VERSION="${OPENAPI_PYTHON_CLIENT_VERSION:-0.28.2}"
RUFF_VERSION="${RUFF_VERSION:-0.15.18}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="$SCRIPT_DIR/.."
SPEC_TMP="$SDK_DIR/.generated/openapi.json"
CLIENT_SPEC_TMP="$SDK_DIR/.generated/openapi.client.json"
OUT_DIR="$SDK_DIR/lemma_sdk/openapi_client"
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

format_generated_python() {
  local target_dir="$1"
  (
    cd "$SDK_DIR"
    uv tool run --from ruff=="$RUFF_VERSION" ruff check --fix --select F401,I "$target_dir"
    uv tool run --from ruff=="$RUFF_VERSION" ruff format "$target_dir"
  )
}

# Derive the OpenAPI URL from LEMMA_API_URL if set (recommended pattern):
#   LEMMA_API_URL=https://api.lemma.work bash generate_openapi_client.sh
# Or explicitly:
#   OPENAPI_URL=https://api.lemma.work/openapi.json bash generate_openapi_client.sh
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
# Strip server-internal surface the public SDK/CLI never calls: billing,
# the job scheduler, and inbound webhook receivers. Excluded operations are
# dropped and their now-unreferenced component schemas are pruned. The list
# itself lives in prepare_client_openapi.py so this script, the TypeScript one
# and dump_openapi_spec.py cannot prune differently.
"$PYTHON_BIN" "$REPO_ROOT/scripts/prepare_client_openapi.py" \
  "$SPEC_TMP" \
  "$CLIENT_SPEC_TMP"
normalize_json_file "$CLIENT_SPEC_TMP"

cd "$SDK_DIR"
uv tool run --from openapi-python-client=="$OPENAPI_PYTHON_CLIENT_VERSION" openapi-python-client generate \
  --path "$CLIENT_SPEC_TMP" \
  --meta none \
  --output-path "$OUT_DIR" \
  --config "$SDK_DIR/openapi-python-client.yaml" \
  --overwrite

format_generated_python "$OUT_DIR"

# openapi-python-client emits a models/__init__.py that eagerly imports every
# generated model, which makes importing a single model load all 450+ (~10s in
# the agentbox runtime). Rewrite it to a lazy PEP 562 __getattr__ loader.
"$PYTHON_BIN" "$SCRIPT_DIR/make_models_init_lazy.py" "$OUT_DIR/models/__init__.py"

# Same treatment for the package top-level __init__: the generated version
# eagerly imports .client (httpx, ~80ms locally / ~1s in the agentbox runtime),
# which every `from ...models.foo import Foo` pays because Python executes the
# parent package first. Keep it lazy so CLI startup never loads the HTTP stack.
cat > "$OUT_DIR/__init__.py" <<'PY'
"""A client library for accessing Lemma Backend"""
# Lazy exports (PEP 562): `.client` pulls in httpx (~80ms), which would
# otherwise be paid by any import of a single generated model, since Python
# executes this parent package first. The CLI imports models at startup, so
# keep this package import free of the HTTP stack.
# Reapplied by scripts/generate_openapi_client.sh after regeneration.
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import AuthenticatedClient, Client

__all__ = (
    "AuthenticatedClient",
    "Client",
)


def __getattr__(name: str):
    if name in __all__:
        from . import client

        value = getattr(client, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
PY

# Stamp the spec identity. The backend is the single source of truth for the API
# version (app/version.py -> FastAPI version -> openapi info.version).
#
# This used to also refuse to generate when the schema shape changed without an
# API_VERSION bump, and auto-bumped the SDK package patch. Both are gone. Every
# branch that touched the schema had to move the version, so a version nobody
# had released still climbed once per PR, and every such branch conflicted with
# every other on the same lines — while the number itself meant nothing, since
# the release it names has not happened.
#
# Skew detection never depended on that label anyway: SPEC_SHA256 below
# fingerprints the schema itself, so `lemma doctor` sees a mismatched client
# whether or not anyone remembered to bump. What must hold is that every
# component agrees at *release* time, which scripts/check_version_consistency.py
# enforces against the tag before anything is published.
"$PYTHON_BIN" - "$CLIENT_SPEC_TMP" "$SDK_DIR/lemma_sdk/openapi_spec.json" "$SDK_DIR/lemma_sdk/_spec_info.py" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

new_spec_path, committed_path, spec_info_path = (Path(p) for p in sys.argv[1:4])

new_text = new_spec_path.read_text(encoding="utf-8")
new_spec = json.loads(new_text)
new_version = str(new_spec.get("info", {}).get("version") or "")
new_sha = hashlib.sha256(new_text.encode("utf-8")).hexdigest()


committed_path.write_text(new_text, encoding="utf-8")
spec_info_path.write_text(
    '"""Identity of the OpenAPI spec this SDK was generated from.\n\n'
    "GENERATED by ``scripts/generate_openapi_client.sh`` — do not edit by hand.\n\n"
    "``API_VERSION`` mirrors the backend ``app.version.API_VERSION`` (the\n"
    "``info.version`` of the spec the client was built against). ``SPEC_SHA256`` is\n"
    "the SHA-256 of the bundled ``openapi_spec.json`` and acts as a precise\n"
    "fingerprint of the schema shape. The ``lemma`` CLI reads these via\n"
    "``lemma --version`` / ``lemma doctor`` to detect drift between the installed\n"
    "SDK and the server it is talking to.\n"
    '"""\n\n'
    "from __future__ import annotations\n\n"
    f'API_VERSION = "{new_version}"\n'
    f'SPEC_SHA256 = "{new_sha}"\n',
    encoding="utf-8",
)
print(f"Stamped lemma_sdk/_spec_info.py (API_VERSION={new_version}, sha256={new_sha[:12]}…)")
PY
echo "Generated typed client in lemma_sdk/openapi_client"
