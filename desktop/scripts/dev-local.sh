#!/usr/bin/env bash
# Run Desktop locally.
#
#   dev-local.sh --source                          run this checkout's code
#   dev-local.sh /absolute/path/to/local-runtime   run a released host pack
#   dev-local.sh --source --control                ...and open Local settings
#
# Opens the workspace, like the packaged app does. This script used to force
# Local settings open because iterating on that page was the only thing it was
# for; now that it can run the whole app from source, that was just the wrong
# window. `--control` brings it back for when Local settings *is* the thing
# being worked on.
#
# `--source` is the one to use while developing: locald supervises the backend
# out of lemma-backend/ through `uv run` and the frontend through `next dev`, so
# the workspace you get is the code you are editing rather than whatever the
# last release shipped. Everything else -- the managed runtime, ports, health
# checks, restart policy -- is identical to the packaged path, because a dev run
# that exercises a different supervisor proves nothing about the real one.
#
# The packaged app keeps its state in ~/Library/Application Support/Lemma. This
# script points every path at a throwaway root instead, so a dev session never
# adopts - or corrupts - the daemon, runtime, or Agent Host identity a real
# install owns.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
dev_support_root="${LEMMA_DESKTOP_APP_SUPPORT_DIR:-/tmp/lemma-desktop-dev}"
source_mode=0
open_control=0
host_pack_root="${LEMMA_DESKTOP_HOST_PACK_ROOT:-}"
for argument in "$@"; do
  case "${argument}" in
    --source) source_mode=1 ;;
    --control) open_control=1 ;;
    -*) echo "Unknown option: ${argument}" >&2; exit 1 ;;
    *) host_pack_root="${argument}" ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "desktop/scripts/dev-local.sh currently supports macOS hosts only." >&2
  exit 1
fi
if (( ! source_mode )) && [[ -z "${host_pack_root}" || ! -f "${host_pack_root}/release.json" ]]; then
  echo "Usage: desktop/scripts/dev-local.sh --source [--control]" >&2
  echo "   or: desktop/scripts/dev-local.sh /absolute/path/to/local-runtime" >&2
  echo "The host pack must contain release.json." >&2
  exit 1
fi

"${repo_root}/desktop/scripts/build-sidecar.sh"

host_triple="$(uname -m)-apple-darwin"
locald_bin="${repo_root}/desktop/binaries/lemma-locald-${host_triple}"
vz_bin="${repo_root}/desktop/binaries/lemma-vz-${host_triple}"
dev_locald_root="${dev_support_root}/locald"

# A desktop daemon is deliberately durable, which in development means a locald
# from an earlier run happily outlives `dev-local.sh` and keeps supervising with
# whatever code it was built from. That is not a theoretical problem: a daemon
# an hour older than the fix under test kept reproducing the bug it fixed, and
# the evidence pointed everywhere except at the stale process.
#
# Ask it to leave, then make sure it did.
if [[ -f "${dev_locald_root}/control.token" ]]; then
  # Ask the daemon who it is before asking it to leave. locald takes its root
  # from the environment, not from argv, so a pattern match on the path finds
  # nothing and would silently spare the very process we need gone -- while
  # `pkill lemma-locald` would take out the real install's daemon too. Its own
  # hello carries the pid, which is exactly the one to insist on.
  stale_pid="$(
    env LEMMA_LOCALD_ROOT="${dev_locald_root}" "${locald_bin}" \
      send '{"cmd":"status","id":"dev-identify"}' 2>/dev/null |
      sed -n 's/.*"event":"hello".*"pid":\([0-9]*\).*/\1/p' | head -1
  )"
  env LEMMA_LOCALD_ROOT="${dev_locald_root}" \
    "${locald_bin}" send '{"cmd":"shutdown-daemon","id":"dev-refresh"}' \
    >/dev/null 2>&1 || true
  if [[ -n "${stale_pid}" ]]; then
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "${stale_pid}" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "${stale_pid}" 2>/dev/null; then
      echo "Previous dev locald (pid ${stale_pid}) ignored shutdown; stopping it." >&2
      kill -9 "${stale_pid}" 2>/dev/null || true
      sleep 1
    fi
  fi
fi

declare -a source_env=()
if (( source_mode )); then
  # The VM guest artifacts are the one thing a checkout cannot build on demand,
  # so reuse the ones an install already downloaded. Read-only: every mutable
  # path still points at the throwaway root below.
  installed_runtime="${LEMMA_DESKTOP_MANAGED_RUNTIME_ROOT:-}"
  if [[ -z "${installed_runtime}" ]]; then
    releases="${HOME}/Library/Application Support/Lemma/runtime/releases"
    installed_runtime="$(
      find "${releases}" -maxdepth 2 -type d -name managed-runtime 2>/dev/null |
        grep -v '/\.' | sort | tail -1
    )"
  fi
  if [[ -z "${installed_runtime}" || ! -d "${installed_runtime}" ]]; then
    echo "No managed runtime found to borrow." >&2
    echo "Install one once (any released Desktop build will do), or build the" >&2
    echo "artifacts with the Release Local Images workflow (publish: false)." >&2
    echo "Override the location with LEMMA_DESKTOP_MANAGED_RUNTIME_ROOT." >&2
    exit 1
  fi

  release_manifest="$(dirname "${installed_runtime}")/local-runtime/release.json"
  if [[ ! -f "${release_manifest}" ]]; then
    echo "Managed runtime at ${installed_runtime} has no sibling release.json." >&2
    exit 1
  fi

  # locald renders the manifest itself, deliberately. It owns the managed
  # runtime's Postgres and Redis passwords -- it generates them and boots the VM
  # with them -- so a manifest rendered out here could not agree with the
  # database the backend is about to connect to.
  source_env=(
    "LEMMA_LOCALD_SOURCE_ROOT=${repo_root}"
    "LEMMA_LOCALD_SOURCE_RELEASE_MANIFEST=${release_manifest}"
    "LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT=${installed_runtime}"
    # A pack root is still required to reach the native renderer; source mode
    # takes precedence over it and never reads it.
    "LEMMA_DESKTOP_HOST_PACK_ROOT=$(dirname "${installed_runtime}")/local-runtime"
  )
  echo "Running Desktop local mode from ${repo_root}"
  echo "  borrowing the managed runtime at ${installed_runtime}"
else
  source_env=("LEMMA_DESKTOP_HOST_PACK_ROOT=${host_pack_root}")
fi

declare -a control_env=()
if (( open_control )); then
  control_env=("LEMMA_DESKTOP_OPEN_CONTROL=1" "LEMMA_DESKTOP_CONTROL_DEBUG=1")
fi

cd "${repo_root}/desktop"
exec env \
  LEMMA_DESKTOP_APP_SUPPORT_DIR="${dev_support_root}" \
  LEMMA_DESKTOP_CONNECTION_MODE="local" \
  LEMMA_DESKTOP_RUNTIME_ROOT="${repo_root}" \
  "${source_env[@]}" \
  ${control_env[@]+"${control_env[@]}"} \
  LEMMA_DESKTOP_LOCALD_BIN="${locald_bin}" \
  LEMMA_DESKTOP_VZ_BIN="${vz_bin}" \
  npx -y @tauri-apps/cli@2.11.4 dev
