"""The time zone a person's days are counted in.

A count per day is only right in the reader's own zone: an evening's work in
New York lands on the next day in UTC. Names are IANA names, checked against
the zones this host knows rather than by building a ``ZoneInfo`` -- on a
case-insensitive filesystem ``ZoneInfo("america/new_york")`` succeeds, and the
same string then fails in the database and on every Linux host. The schedule
module checks its zones the same way, for the same reason.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

#: What a read that names no zone counts in.
DEFAULT_ZONE = "UTC"


@lru_cache(maxsize=1)
def _known_zones() -> frozenset[str]:
    """Every zone name this host resolves, read once: the set is fixed for the
    life of the process, and reading it walks the tz database."""
    return frozenset(available_timezones()) | {DEFAULT_ZONE}


def known_zone(name: str | None) -> ZoneInfo:
    """The zone ``name`` names, or raise ``ValueError``. Blank is UTC."""
    text = (name or "").strip() or DEFAULT_ZONE
    if text not in _known_zones():
        raise ValueError(
            f"Unknown time zone: {name!r}. Use an IANA zone name such as "
            "'Europe/Berlin' or 'America/New_York'."
        )
    return ZoneInfo(text)


def last_days(
    *, now: datetime, zone: ZoneInfo, days: int
) -> tuple[list[date], datetime]:
    """The last ``days`` days in ``zone``, oldest first and ending today, and
    the moment the first of them began.

    Counted on the calendar, not in 24-hour steps back from now, so a
    daylight-saving change moves no day's boundary.
    """
    today = now.astimezone(zone).date()
    first = today - timedelta(days=days - 1)
    return (
        [first + timedelta(days=offset) for offset in range(days)],
        datetime.combine(first, time.min, tzinfo=zone),
    )
