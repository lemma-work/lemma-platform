"""Cron parsing and next-fire-time, without a scheduler attached.

Schedules are stored as five-field UTC cron strings and have to be turned into
"when does this fire next" in three unrelated places: validation at write time,
the poller that decides what is due, and the reconciler that rebuilds state at
startup. None of those want a scheduler; they want a parsed expression.

This wraps ``crontab.CronTab`` rather than APScheduler's ``CronTrigger``. Both
parse the same syntax, but ``CronTrigger`` drags in the scheduler package for
what is a pure calculation, and it was the reason APScheduler could not simply
be deleted: ``validate_cron_expression`` returned a trigger object that the job
store then consumed. ``crontab`` is already present as a streaq dependency, so
this removes a direct dependency without adding one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from crontab import CronTab

_CRON_FIELDS = 5


@dataclass(frozen=True, slots=True)
class CronSchedule:
    """A validated five-field UTC cron expression.

    Frozen and carrying its own source text so it can be logged, compared and
    round-tripped through a database row without re-parsing being a correctness
    question.
    """

    expression: str
    _tab: CronTab

    @classmethod
    def parse(cls, expression: str) -> "CronSchedule":
        """Parse, or raise ``ValueError``.

        Field count is checked before handing the string over: ``crontab``
        accepts a six-field form where the first field is seconds, and a
        schedule that fires every thirty seconds because someone added a field
        is not something to discover in production.
        """
        text = expression.strip()
        if len(text.split()) != _CRON_FIELDS:
            raise ValueError(
                f"Invalid cron expression: {expression}. Expected five fields."
            )
        return cls(expression=text, _tab=CronTab(text))

    def next_fire_time(self, after: datetime) -> datetime | None:
        """The first fire strictly after ``after``, in UTC, or ``None``.

        ``after`` is normalised to UTC rather than trusted: a naive datetime
        here would silently be interpreted in the host's local zone, and every
        schedule in the system is defined in UTC.

        ``None`` means the expression has no fire within the library's lookahead
        horizon -- reachable with a rare expression like ``0 0 29 2 *`` walked
        far enough into the future. A caller polling for due work should treat
        it as "nothing more to schedule" rather than an error; it is not a
        parse failure, and the expression stays valid.
        """
        moment = after if after.tzinfo is not None else after.replace(tzinfo=timezone.utc)
        moment = moment.astimezone(timezone.utc)
        # `crontab` ships no stubs for `CronTab.next`, so basedpyright cannot
        # see it. Narrowed to this one call rather than silenced file-wide: the
        # rest of this module is exactly the code the critical-types gate should
        # be checking, and cron arithmetic is where a silent type error would
        # cost a schedule firing at the wrong time.
        seconds = self._tab.next(  # pyright: ignore[reportAttributeAccessIssue]
            moment, default_utc=True
        )
        if seconds is None:
            return None
        return moment + timedelta(seconds=float(seconds))

    def fire_times_from(self, start: datetime, *, limit: int):
        """Successive fire times, for validating spacing without a scheduler.

        Stops early rather than yielding ``None`` when the horizon is reached.
        """
        cursor = start
        for _ in range(limit):
            nxt = self.next_fire_time(cursor)
            if nxt is None:
                return
            cursor = nxt
            yield cursor
