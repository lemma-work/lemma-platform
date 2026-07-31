#!/usr/bin/env bash
# Run Desktop against a locally built host pack with Local settings open.
#
# The packaged app keeps its state in ~/Library/Application Support/Lemma. This
# script points every path at a throwaway root instead, so a dev session never
# adopts - or corrupts - the daemon, runtime, or Agent Host identity a real
# install owns.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
host_pack_root="${LEMMA_DESKTOP_HOST_PACK_ROOT:-${1:-}}"
dev_support_root="${LEMMA_DESKTOP_APP_SUPPORT_DIR:-/tmp/lemma-desktop-dev}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "desktop/scripts/dev-local.sh currently supports macOS hosts only." >&2
  exit 1
fi
if [[ -z "${host_pack_root}" || ! -f "${host_pack_root}/release.json" ]]; then
  echo "Usage: desktop/scripts/dev-local.sh /absolute/path/to/local-runtime" >&2
  echo "The host pack must contain release.json." >&2
  exit 1
fi

"${repo_root}/desktop/scripts/build-sidecar.sh"

host_triple="$(uname -m)-apple-darwin"
locald_bin="${repo_root}/desktop/binaries/lemma-locald-${host_triple}"
vz_bin="${repo_root}/desktop/binaries/lemma-vz-${host_triple}"
dev_locald_root="${dev_support_root}/locald"

# A desktop daemon is deliberately durable. Retire the prior isolated dev
# daemon so every run exercises the freshly compiled locald sidecar.
if [[ -f "${dev_locald_root}/control.token" ]]; then
  env LEMMA_LOCALD_ROOT="${dev_locald_root}" \
    "${locald_bin}" send '{"cmd":"shutdown-daemon","id":"dev-refresh"}' \
    >/dev/null 2>&1 || true
fi

cd "${repo_root}/desktop"
exec env \
  LEMMA_DESKTOP_APP_SUPPORT_DIR="${dev_support_root}" \
  LEMMA_DESKTOP_CONNECTION_MODE="local" \
  LEMMA_DESKTOP_RUNTIME_ROOT="${repo_root}" \
  LEMMA_DESKTOP_HOST_PACK_ROOT="${host_pack_root}" \
  LEMMA_DESKTOP_LOCALD_BIN="${locald_bin}" \
  LEMMA_DESKTOP_VZ_BIN="${vz_bin}" \
  LEMMA_DESKTOP_OPEN_CONTROL="1" \
  LEMMA_DESKTOP_CONTROL_DEBUG="1" \
  npx -y @tauri-apps/cli@2.11.4 dev
