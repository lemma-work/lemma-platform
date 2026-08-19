#!/usr/bin/env bash
# Reproduce a flaky test under artificial CPU load, without leaking the load.
#
# An earlier ad hoc version of this loop spawned N `while :; do :; done`
# background CPU hogs, ran a test binary hundreds of times, then did
# `kill $(jobs -p)` as its last line. That bare kill only runs if the script
# reaches its own last line — if the invoking process is killed, times out, or
# the session running it just ends first, the busy-loop children are orphaned
# and each pins 100% of a CPU core forever. One instance ran for 20+ hours
# before it was noticed, on a machine with several of them stacked up.
#
# This version traps cleanup so it fires on normal exit, error, Ctrl-C, AND an
# external SIGTERM (e.g. a wrapper script's timeout) — the case the old
# version couldn't handle — and enforces its own hard runtime ceiling so a
# hung test loop can't keep the load generators alive indefinitely even if
# something else goes wrong.
#
# Usage:
#   desktop/scripts/stress_test_under_load.sh <cargo-test-filter> [iterations] [cpu_hogs] [max_runtime_seconds]
#
# Example (reproduce a desktop_context/native_material-style flake):
#   desktop/scripts/stress_test_under_load.sh "desktop_context native_material" 400 12 1800

set -euo pipefail

filter="${1:?usage: $0 <cargo-test-filter> [iterations] [cpu_hogs] [max_runtime_seconds]}"
iterations="${2:-100}"
cpu_hogs="${3:-$(( $(sysctl -n hw.ncpu 2>/dev/null || nproc) - 1 ))}"
max_runtime_seconds="${4:-1800}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

load_pids=()
watchdog_pid=""

cleanup() {
    if [[ -n "$watchdog_pid" ]]; then
        kill "$watchdog_pid" 2>/dev/null || true
    fi
    if [[ ${#load_pids[@]} -gt 0 ]]; then
        kill "${load_pids[@]}" 2>/dev/null || true
        wait "${load_pids[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

echo "Spawning $cpu_hogs CPU load generator(s)…"
for _ in $(seq 1 "$cpu_hogs"); do
    (while :; do :; done) &
    load_pids+=("$!")
done
echo "Load pids: ${load_pids[*]}"

# Hard backstop: if the test loop below hangs, this still tears everything
# down after max_runtime_seconds rather than running forever.
(sleep "$max_runtime_seconds" && kill -TERM $$ 2>/dev/null) &
watchdog_pid=$!

sleep 1

fail=0
for i in $(seq 1 "$iterations"); do
    if ! cargo test --quiet -- "$filter" --test-threads 16 >/dev/null 2>&1; then
        fail=$((fail + 1))
        echo "FAIL run $i"
    fi
done

echo "failures=$fail/$iterations"
