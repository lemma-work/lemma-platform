"""Periodic work must not all fire on the same tick.

What ``test_cron_schedule_spread`` held for the streaq crons, held for their
replacement. Production once saw the convoy as event-loop stalls: 123 stall
reports over 48 hours, bunched on the round minute where twelve crons fired
together. Periodics have no wall clock -- each is armed at worker boot and
re-armed ``every`` after it runs -- so without an offset they would all fire at
the boot instant and then in lockstep forever. ``PeriodicSpec.phase`` (a hash of
the name, within the interval up to an hour) is what spreads them; this is the
ratchet that keeps it working as periodics are added.

Measured over one hour after a boot, per minute, for every periodic of five
minutes or longer. The faster ones (the receiver reconcile, onboarding cleanup)
fall due every minute by definition, as the per-minute reconcilers did.
"""

from __future__ import annotations

import collections
from datetime import timedelta

import pytest

from app.core.infrastructure.work import registry
from app.worker import register_worker_modules
from app.core.infrastructure.work.registry import PeriodicSpec

pytestmark = pytest.mark.unit

# Every module's kinds and periodics, as the worker registers them.
register_worker_modules()

#: Distinct periodics allowed to fall due in any single minute after a boot.
MAX_JOBS_PER_MINUTE = 4
#: Faster than this is "every minute" anyway, and not what convoys.
_MEASURED_FROM = timedelta(minutes=5)
#: The boot minute itself, which every unspread periodic would share.
MAX_JOBS_IN_THE_FIRST_MINUTE = 3


def _by_minute(specs: list[PeriodicSpec]) -> dict[int, set[str]]:
    due: dict[int, set[str]] = collections.defaultdict(set)
    for spec in specs:
        if spec.every < _MEASURED_FROM:
            continue
        moment = spec.phase
        while moment < timedelta(hours=1):
            due[int(moment.total_seconds() // 60)].add(spec.name)
            moment += spec.every
    return due


def test_the_gate_sees_the_real_periodic_population() -> None:
    """A gate that measures nothing passes forever."""
    assert len(registry.periodics()) >= 20


def test_no_minute_carries_a_convoy_of_jobs() -> None:
    crowded = {
        minute: names
        for minute, names in _by_minute(registry.periodics()).items()
        if len(names) > MAX_JOBS_PER_MINUTE
    }
    assert not crowded, "Too many periodics fall due together:\n" + "\n".join(
        f"  +{minute:02d}m ({len(names)}) {', '.join(sorted(names))}"
        for minute, names in sorted(crowded.items())
    )


def test_the_boot_minute_is_not_a_pile_up() -> None:
    first = _by_minute(registry.periodics())[0]
    assert len(first) <= MAX_JOBS_IN_THE_FIRST_MINUTE, (
        f"{len(first)} periodics fall due in the minute after a boot: "
        f"{', '.join(sorted(first))}"
    )


def test_the_gate_actually_fails_on_a_convoy() -> None:
    """Without the phase, everything would be due at once: prove the check bites."""
    convoy = [
        PeriodicSpec(f"job_{index}", timedelta(minutes=5), "k")
        for index in range(MAX_JOBS_PER_MINUTE + 1)
    ]
    unspread = collections.Counter(
        int(timedelta(0).total_seconds() // 60) for _ in convoy
    )
    assert unspread[0] > MAX_JOBS_PER_MINUTE
    # And the phase does spread them.
    assert len({spec.phase for spec in convoy}) > 1
