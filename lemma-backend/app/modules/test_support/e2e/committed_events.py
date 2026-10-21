"""What domain events this process committed, for tests that assert on them.

The unit of work hands committed events to the after-commit sink
(``app.core.analytics.domain_events``) and writes them nowhere durable, so a
test that used to read ``domain_event_outbox`` records them here instead:
in-process e2e requests commit in the test's own process.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pytest

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db import uow as uow_module


class CommittedEvents(list[DomainEvent]):
    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        """The JSON payloads of every committed event of ``event_type``."""
        return [
            event.model_dump(mode="json")
            for event in self
            if event.event_type == event_type
        ]


@contextmanager
def capture_committed_events(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[CommittedEvents]:
    captured = CommittedEvents()
    offer = uow_module.offer_committed

    def recording(events: Sequence[DomainEvent]) -> None:
        captured.extend(events)
        offer(events)

    monkeypatch.setattr(uow_module, "offer_committed", recording)
    yield captured


@pytest.fixture
def committed_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[CommittedEvents]:
    with capture_committed_events(monkeypatch) as captured:
        yield captured
