from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from lemma_sdk.errors import LemmaNotFoundError
from lemma_sdk.resources.schedules import PodSchedules

POD = uuid4()


class RecordingTransport:
    """Answers the one listing the resolver makes, and remembers how it asked."""

    def __init__(self, items: list[Any]) -> None:
        self.items = items
        self.calls: list[dict[str, Any]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(items=self.items)


def test_name_lookup_asks_the_server_instead_of_scanning_a_listing() -> None:
    # It used to list up to 1000 schedules and match client-side, so every
    # name-addressed call paid for a full page and a pod past the cap was told
    # a schedule it owns does not exist.
    schedule_id = uuid4()
    transport = RecordingTransport([SimpleNamespace(name="nightly", id=schedule_id)])

    resolved = PodSchedules(transport, pod_id=POD)._schedule_id("nightly")

    assert resolved == schedule_id
    assert transport.calls[0]["name"] == "nightly"
    assert transport.calls[0]["limit"] == 2


def test_a_uuid_resolves_without_any_request() -> None:
    transport = RecordingTransport([])
    schedule_id = uuid4()

    assert PodSchedules(transport, pod_id=POD)._schedule_id(str(schedule_id)) == (
        schedule_id
    )
    assert transport.calls == []


def test_a_missing_name_raises_the_same_error_as_any_missing_resource() -> None:
    # Not a bare ValueError: `except LemmaNotFoundError` is how every other
    # missing resource in this SDK is caught.
    transport = RecordingTransport([])

    with pytest.raises(LemmaNotFoundError) as excinfo:
        PodSchedules(transport, pod_id=POD)._schedule_id("no-such-schedule")

    message = str(excinfo.value)
    assert "no-such-schedule" in message
    assert "schedule id" in message


def test_resolution_is_shared_by_every_name_addressed_call() -> None:
    schedule_id = uuid4()
    transport = RecordingTransport([SimpleNamespace(name="nightly", id=schedule_id)])
    schedules = PodSchedules(transport, pod_id=POD)

    for call in (schedules.get, schedules.delete):
        transport.calls.clear()
        call("nightly")
        assert transport.calls[0]["name"] == "nightly"
