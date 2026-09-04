"""Periodic jobs must not all fire on the same tick.

The worker runs one event loop, deliberately at ``replicas: 1``. Every cron
written on a round boundary therefore lands on the same turn of that loop, and
the pile-up is what production saw as event-loop stalls: 123 stall reports over
48 hours, distributed by minute-of-hour as

    :00  29   :30  11   :45  7   :15  5   everything else  <= 3

which is a crontab, not a mystery. At ``:00`` twelve jobs fired together — two
per-minute reconcilers, six ``*/5`` jobs, and the ``*/10``, ``*/15``, ``*/30``
and hourly prunes all coinciding.

Staggering is cheap and already the local convention: ``17 * * * *``,
``20 * * * *``, ``41 * * * *`` and ``23 4 * * *`` were deliberately placed off
the hour before this. The round-boundary jobs were what remained.

This test is the ratchet. Adding ``*/5 * * * *`` is the natural thing to write
and re-forms the convoy one job at a time, so the bound is enforced rather than
documented.
"""

from __future__ import annotations

import collections
from datetime import datetime, timedelta

import pytest
from crontab import CronTab

from app.core.infrastructure.jobs.streaq_runtime import LANE_WORKERS
from app.core.registry.assembly import import_module_tasks
from app.core.registry.installed import OSS_MODULES

pytestmark = pytest.mark.unit

#: Jobs allowed to fire in any single minute. Two per-minute reconcilers are
#: unavoidable by definition; this leaves room for a couple of periodic jobs to
#: share a tick without letting a convoy re-form.
MAX_JOBS_PER_MINUTE = 6

#: The round minute is the one that actually hurt, so it is held tighter.
MAX_JOBS_ON_THE_HOUR = 4

_REFERENCE_HOUR = datetime(2026, 1, 1, 0, 0, 0)


@pytest.fixture(scope="module")
def cron_jobs() -> dict[str, str]:
    """Every registered cron, as ``name -> crontab``.

    Read off the streaq workers rather than scanned out of the source, so a cron
    whose schedule comes from settings (``sweep_workspace_sandboxes``) is
    measured at its real value.
    """
    # Core-owned crons live outside the module registry.
    import app.core.infrastructure.events.tasks  # noqa: F401

    import_module_tasks(OSS_MODULES)
    return {
        name: str(task.crontab)
        for worker in LANE_WORKERS.values()
        for name, task in worker.registry.items()
        if getattr(task, "crontab", None) is not None
    }


def _fires_at(tab: str, moment: datetime) -> bool:
    """True when ``tab`` fires exactly at ``moment``."""
    just_before = moment - timedelta(seconds=1)
    return int(CronTab(tab).next(just_before, default_utc=True)) <= 1


def _by_minute(cron_jobs: dict[str, str]) -> dict[int, list[str]]:
    firing: dict[int, list[str]] = collections.defaultdict(list)
    for minute in range(60):
        moment = _REFERENCE_HOUR + timedelta(minutes=minute)
        for name, tab in cron_jobs.items():
            if _fires_at(tab, moment):
                firing[minute].append(name)
    return firing


def test_the_gate_sees_the_real_cron_population(cron_jobs):
    """A gate that measures nothing passes forever."""
    assert len(cron_jobs) >= 15, (
        f"only {len(cron_jobs)} crons registered; the registry probably stopped "
        "importing handler modules"
    )


def test_no_minute_carries_a_convoy_of_jobs(cron_jobs):
    firing = _by_minute(cron_jobs)
    crowded = {
        minute: names
        for minute, names in firing.items()
        if len(names) > MAX_JOBS_PER_MINUTE
    }

    assert not crowded, "Too many periodic jobs share a tick:\n" + "\n".join(
        f"  :{minute:02d} ({len(names)}) {', '.join(sorted(names))}"
        for minute, names in sorted(crowded.items())
    )


def test_the_top_of_the_hour_is_not_a_pile_up(cron_jobs):
    """``:00`` is the default everyone reaches for, so it is the one that fills."""
    on_the_hour = _by_minute(cron_jobs)[0]

    assert len(on_the_hour) <= MAX_JOBS_ON_THE_HOUR, (
        f"{len(on_the_hour)} jobs fire at :00 — {', '.join(sorted(on_the_hour))}.\n"
        "Give the new one an offset (`7 * * * *`, `1-59/5 * * * *`) rather than a "
        "round boundary; the worker is single-loop and they contend for one turn."
    )


def test_the_gate_actually_fails_on_a_convoy():
    """Prove the check bites, so a silent pass cannot be mistaken for health."""
    convoy = {f"job_{index}": "*/5 * * * *" for index in range(MAX_JOBS_PER_MINUTE + 1)}

    firing = _by_minute(convoy)

    assert len(firing[0]) > MAX_JOBS_PER_MINUTE
