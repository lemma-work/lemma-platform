"""What a value read from a Public table becomes on a page."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.datastore.contracts.public_tables import public_value

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (3, 3),
        ("chat", "chat"),
        (
            datetime(2026, 10, 15, 13, 30, tzinfo=timezone.utc),
            "2026-10-15T13:30:00+00:00",
        ),
        (date(2026, 10, 15), "2026-10-15"),
        (Decimal("2.5"), 2.5),
        (UUID(int=7), "00000000-0000-0000-0000-000000000007"),
        (
            {"rooms": ["A", "B"], "opens": date(2026, 10, 15)},
            {"rooms": ["A", "B"], "opens": "2026-10-15"},
        ),
    ],
)
def test_values_become_what_json_can_carry(value, expected):
    assert public_value(value) == expected
