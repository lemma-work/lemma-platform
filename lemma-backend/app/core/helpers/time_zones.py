"""Time zone names the tz database has renamed, read as their current names.

Browsers still report some zones by a name IANA has retired -- Chrome answers
``Asia/Calcutta`` for India -- and whether a host can read the old name depends
on its tz data, not on Python: Debian 13 moved the old names out of ``tzdata``
into a separate ``tzdata-legacy`` package, so an image or a database built
without it knows only the current ones. Reading the old name as the current one
is what lets every host agree. Here rather than in one module because a
person's settings and a schedule both take their zone from the same browsers.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from types import MappingProxyType

#: The names CLDR still calls canonical where IANA has moved on, which is what
#: a browser hands over, mapped to the name IANA uses now.
RENAMED_ZONES: Mapping[str, str] = MappingProxyType(
    {
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
)


def current_zone_name(name: str, known: Collection[str]) -> str:
    """``name``, or the name it was renamed to when ``known`` -- the zones this
    host resolves -- has that one.

    Only when the host has the new name: tz data older than a rename knows the
    old name alone, and answering with a name the host cannot read would trade
    one failure for another.
    """
    current = RENAMED_ZONES.get(name)
    return current if current is not None and current in known else name
