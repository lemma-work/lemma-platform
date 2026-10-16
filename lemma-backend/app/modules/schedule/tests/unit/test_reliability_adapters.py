"""The dedup key a fire event carries.

The ``source_event_id`` is what makes a double fire collapse into one run, and
it has to stay canonical no matter what is doing the firing.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.schedule.domain.events.schedule import ScheduleFired


def test_schedule_fired_requires_canonical_source_event_id() -> None:
    with pytest.raises(ValidationError):
        ScheduleFired.model_validate(
            {
                "schedule_id": str(uuid4()),
                "user_id": str(uuid4()),
                "schedule_type": "TIME",
                "payload": {},
            }
        )
