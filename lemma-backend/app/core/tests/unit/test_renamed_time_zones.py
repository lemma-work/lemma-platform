"""Reading a retired zone name as its current one, against the zones a host has.

Which names a host resolves is its tz data's business, so each case here names
the host it describes instead of depending on the one the tests run on.
"""

from __future__ import annotations

from app.core.helpers.time_zones import current_zone_name

#: Current tz data on a distribution that ships the old names separately.
_CURRENT_NAMES_ONLY = frozenset({"UTC", "Asia/Kolkata", "Europe/Kyiv"})
#: tz data from before ``Europe/Kyiv`` was named.
_BEFORE_THE_RENAME = frozenset({"UTC", "Europe/Kiev"})


def test_a_retired_name_is_answered_by_the_current_one_the_host_has() -> None:
    assert current_zone_name("Asia/Calcutta", _CURRENT_NAMES_ONLY) == "Asia/Kolkata"
    assert current_zone_name("Europe/Kiev", _CURRENT_NAMES_ONLY) == "Europe/Kyiv"


def test_a_host_older_than_the_rename_keeps_the_old_name() -> None:
    """Answering ``Europe/Kyiv`` there would refuse a zone the host can read."""
    assert current_zone_name("Europe/Kiev", _BEFORE_THE_RENAME) == "Europe/Kiev"


def test_a_name_that_was_never_renamed_is_returned_as_given() -> None:
    assert current_zone_name("Europe/Berlin", _CURRENT_NAMES_ONLY) == "Europe/Berlin"
    assert current_zone_name("Asia/Bombay", _CURRENT_NAMES_ONLY) == "Asia/Bombay"
