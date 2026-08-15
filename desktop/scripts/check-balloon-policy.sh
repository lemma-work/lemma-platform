#!/usr/bin/env bash
# The memory balloon crashed a guest once, and the Swift package it lives in has
# no test target. This is the guard in place of one: it checks that the policy
# which stopped the crash is still expressed in the source.
#
# It is a source check, not a test, and it says so. It cannot prove the balloon
# behaves — only that the decisions below have not been quietly removed. Each
# assertion names the failure it stands for so a person hitting one can judge
# whether the rule still applies rather than deleting the line.
#
# What went wrong, in one paragraph: `active_sandboxes == 0` was read as "idle",
# which during first setup means "nothing has started yet". Sixty seconds into a
# first boot, with Postgres mid-initdb, the balloon asked for 4.5 of 6 GiB back
# in one step and the kernel took an Oops in migrate_pages. Setup then failed
# with migrations timing out after 300s, none of which named memory.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_file="$root/local-runtime/macos-vz/Sources/LemmaVZ/main.swift"

if [[ ! -f "$source_file" ]]; then
    echo "  ✗ balloon source not found at $source_file" >&2
    exit 1
fi

failed=0

require() {
    local needle="$1" why="$2"
    if ! grep -qF -- "$needle" "$source_file"; then
        printf '  ✗ %s\n      expected to find: %s\n' "$why" "$needle" >&2
        failed=1
    fi
}

refuse() {
    local needle="$1" why="$2"
    if grep -qF -- "$needle" "$source_file"; then
        printf '  ✗ %s\n      found again: %s\n' "$why" "$needle" >&2
        failed=1
    fi
}

# 1. The clock is its own. `observe` is called from `annotate`, which runs only
#    on a health reply — and locald suppresses health polling while a long local
#    operation runs. Driving the countdown from reply arrival meant it could only
#    ever complete while the guest was busy, which is the crash.
require 'private func reconsider()' \
    'the balloon must decide on its own timer, not when a health reply arrives'
require 'private func scheduleTick()' \
    'the timer that drives reconsider() is gone'

# 2. Silence is unknown, never idle. A gap in health replies is evidence of work.
require 'observationValidSeconds' \
    'a stale observation must stop counting as evidence the guest is idle'

# 3. Nothing is reclaimed during first setup, which is minutes of real work with
#    no sandbox to show for it.
require 'bootGraceSeconds' \
    'the balloon must hold off after boot, when no sandbox count means anything'

# 4. It walks rather than jumps: reclaiming is page migration in the guest, and
#    the cost is superlinear in the size of a single ask.
require 'stepBytes' 'the shrink must step rather than jump'
require 'private func stepDown()' 'the stepping walk is gone'
refuse 'device.targetVirtualMachineMemorySize = min(self.idleTarget' \
    'the one-shot jump to the idle target is what the guest could not survive'

# 5. "Has ever run a sandbox" is not the gate. It covered first setup, but never
#    expired — so a guest that legitimately never runs a sandbox held its whole
#    ceiling forever and reported `starting` for the life of the process.
refuse 'hasEverBeenBusy' \
    'ever-been-busy never expires; the boot grace period replaced it'

if [[ $failed -ne 0 ]]; then
    echo "  ✗ balloon policy check failed — see desktop/scripts/check-balloon-policy.sh" >&2
    exit 1
fi

echo "  ✓ balloon policy intact"
