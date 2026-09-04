"""Cron parsing and next-fire-time, without a scheduler attached.

Schedules are stored as five-field cron strings plus the IANA zone they are
read in, and have to be turned into "when does this fire next" in three
unrelated places: validation at write time, the poller that decides what is
due, and the reconciler that rebuilds state at startup. None of those want a
scheduler; they want a parsed expression.

This wraps ``crontab.CronTab`` rather than APScheduler's ``CronTrigger``. Both
parse the same syntax, but ``CronTrigger`` drags in the scheduler package for
what is a pure calculation, and it was the reason APScheduler could not simply
be deleted: ``validate_cron_expression`` returned a trigger object that the job
store then consumed. ``crontab`` is already present as a streaq dependency, so
this removes a direct dependency without adding one.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from crontab import CronTab

_CRON_FIELDS = 5

# How many wall-clock occurrences may be walked past before giving up on
# finding one that is a real instant strictly after the moment asked about.
# Only a daylight-saving fold makes more than one step necessary, and the
# largest shift in the tz database is two hours, so a minute-cron needs at most
# 120. The cap is set well clear of that rather than tight, because exhausting
# it reads as "this expression has no next fire" and retires the schedule.
_MAX_WALL_CLOCK_STEPS = 4096


@lru_cache(maxsize=1)
def _known_zones() -> frozenset[str]:
    """Every zone name this host can resolve, read once.

    ``available_timezones()`` walks the whole tz database directory, which is
    not something to do on the write path of every schedule. The set is
    immutable for the life of the process, so an ``lru_cache`` singleton is the
    right shape rather than a data cache.
    """
    return frozenset(available_timezones())


def resolve_zone(name: str | None) -> tzinfo:
    """The zone a schedule's wall-clock times are read in, or raise ``ValueError``.

    ``None`` -- the ``timezone`` key absent from a schedule's config -- is UTC,
    which is what every schedule written before zones existed already meant.
    Absence and the literal string ``"UTC"`` therefore behave identically, so
    nothing has to rewrite existing configs to gain the field, and an exported
    pod bundle does not grow a spurious ``"timezone": "UTC"`` diff.

    Membership of ``available_timezones()`` rather than a successful
    ``ZoneInfo(name)``: ``ZoneInfo`` resolves its key as a *path* under
    ``TZPATH``, so on a case-insensitive filesystem -- macOS by default --
    ``ZoneInfo("america/new_york")`` succeeds, while the same string raises on
    Linux. Validating by construction would accept on a developer's laptop a
    schedule the container then cannot arm.
    """
    if name is None:
        return timezone.utc
    text = name.strip()
    if not text:
        return timezone.utc
    if text not in _known_zones():
        raise ValueError(
            f"Unknown time zone: {name!r}. Use an IANA zone name such as "
            "'Europe/Berlin' or 'America/New_York'."
        )
    return ZoneInfo(text)


def _as_utc(moment: datetime) -> datetime:
    """Read a naive datetime as UTC rather than in the host's local zone."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class CronSchedule:
    """A validated five-field cron expression and the zone it is read in.

    Frozen and carrying its own source text so it can be logged, compared and
    round-tripped through a database row without re-parsing being a correctness
    question.
    """

    expression: str
    zone_name: str | None
    _tab: CronTab
    _zone: tzinfo

    @classmethod
    def parse(cls, expression: str, *, zone: str | None = None) -> "CronSchedule":
        """Parse, or raise ``ValueError``.

        Field count is checked before handing the string over: ``crontab``
        accepts a six-field form where the first field is seconds, and a
        schedule that fires every thirty seconds because someone added a field
        is not something to discover in production.

        ``zone`` is an IANA name; ``None`` means UTC.
        """
        text = expression.strip()
        if len(text.split()) != _CRON_FIELDS:
            raise ValueError(
                f"Invalid cron expression: {expression}. Expected five fields."
            )
        resolved = resolve_zone(zone)
        normalized_zone = zone.strip() if zone and zone.strip() else None
        return cls(
            expression=text,
            zone_name=normalized_zone,
            _tab=CronTab(text),
            _zone=resolved,
        )

    def next_fire_time(self, after: datetime) -> datetime | None:
        """The first fire strictly after ``after``, as a UTC instant, or ``None``.

        The expression is read in this schedule's zone, because ``0 9 * * *``
        means nine in the morning where the person who wrote it lives: in
        ``Europe/Berlin`` that is 08:00Z in summer and 07:00Z in winter.
        ``after`` is normalised to UTC rather than trusted -- a naive datetime
        here would silently be interpreted in the host's local zone.

        Daylight saving is decided here, once, because everything downstream
        works in UTC instants and cannot see the ambiguity:

        * **Spring forward.** The wall-clock hour is skipped, so on that date
          ``0 2 * * *`` has no 02:00 to fire at. It fires at the instant 02:00
          *would* have been -- ``fold=0``, the pre-transition offset -- which
          reads locally as 03:00. The occurrence happens, an hour later than
          usual, rather than being dropped for the day.
        * **Fall back.** The wall-clock hour happens twice, so ``0 1 * * *`` has
          two candidate instants. It fires on the first, pre-transition one
          (``fold=0``) and not on the second: a daily schedule fires once a day.

        ``None`` means the expression has no fire within the library's lookahead
        horizon -- reachable with a rare expression like ``0 0 29 2 *`` walked
        far enough into the future. A caller polling for due work should treat
        it as "nothing more to schedule" rather than an error; it is not a parse
        failure, and the expression stays valid.
        """
        target = _as_utc(after)
        # Walk the expression in wall clock, which is what the author of the
        # expression meant, and convert each occurrence back to an instant.
        cursor = target.astimezone(self._zone).replace(tzinfo=None)
        for _ in range(_MAX_WALL_CLOCK_STEPS):
            # `crontab` ships no stubs for `CronTab.next`, so basedpyright
            # cannot see it. Narrowed to this one call rather than silenced
            # file-wide: the rest of this module is exactly the code the
            # critical-types gate should be checking, and cron arithmetic is
            # where a silent type error would cost a schedule firing at the
            # wrong time.
            seconds = self._tab.next(  # pyright: ignore[reportAttributeAccessIssue]
                cursor, default_utc=True
            )
            if seconds is None:
                return None
            cursor = cursor + timedelta(seconds=float(seconds))
            fire = cursor.replace(tzinfo=self._zone).astimezone(timezone.utc)
            # Strictly-after is load-bearing rather than defensive.
            # `claim_due_schedules` advances a schedule's cursor to whatever
            # this returns, so a value at or before `after` would make the
            # poller re-claim the occurrence it has just fired, forever. Inside
            # the repeated hour of a fall-back transition two instants share one
            # wall clock and `fold=0` maps both back to the earlier one, so this
            # is reachable whenever the caller asks from a real "now" during
            # that hour -- not only from a malformed cursor.
            if fire > target:
                return fire
        return None

    def fire_times_from(self, start: datetime, *, limit: int) -> Iterator[datetime]:
        """Successive fire times, for validating spacing without a scheduler.

        Yields UTC instants, so the spacing a caller measures is real elapsed
        time in this schedule's zone rather than wall-clock distance.

        Stops early rather than yielding ``None`` when the horizon is reached.
        """
        cursor = start
        for _ in range(limit):
            nxt = self.next_fire_time(cursor)
            if nxt is None:
                return
            cursor = nxt
            yield cursor
