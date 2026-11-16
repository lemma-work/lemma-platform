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


#: Zones the tz database renamed, by the old name browsers still report:
#: Chrome answers ``Asia/Calcutta`` for India. This host keeps the old names
#: as links, so they pass the check below -- but a database built on a
#: distribution that ships only the current names does not, and the day count
#: fails there with "time zone not recognized". Read as the current name, the
#: host and the database agree. The list is the names CLDR still calls
#: canonical where IANA has moved on, which is what a browser hands over.
RENAMED_ZONES: dict[str, str] = {
    "Africa/Asmera": "Africa/Asmara",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "America/Catamarca": "America/Argentina/Catamarca",
    "America/Coral_Harbour": "America/Atikokan",
    "America/Cordoba": "America/Argentina/Cordoba",
    "America/Godthab": "America/Nuuk",
    "America/Indianapolis": "America/Indiana/Indianapolis",
    "America/Jujuy": "America/Argentina/Jujuy",
    "America/Louisville": "America/Kentucky/Louisville",
    "America/Mendoza": "America/Argentina/Mendoza",
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Rangoon": "Asia/Yangon",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Atlantic/Faeroe": "Atlantic/Faroe",
    "Europe/Kiev": "Europe/Kyiv",
    "Pacific/Enderbury": "Pacific/Kanton",
    "Pacific/Ponape": "Pacific/Pohnpei",
    "Pacific/Truk": "Pacific/Chuuk",
}


def known_zone(name: str | None) -> ZoneInfo:
    """The zone ``name`` names, or raise ``ValueError``. Blank is UTC.

    A renamed zone is answered by its current name (``RENAMED_ZONES``), so
    what is handed on to the database is a name every tz database has.
    """
    text = (name or "").strip() or DEFAULT_ZONE
    current = RENAMED_ZONES.get(text)
    if current is not None and current in _known_zones():
        text = current
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
