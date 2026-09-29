"""Both Resend routes resolve a mailbox to a surface the same way."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agent_surfaces.services.resend_recipients import (
    surface_for_recipients,
)


class _Repository:
    def __init__(self, by_address: dict[str, object]) -> None:
        self._by_address = by_address
        self.asked: list[tuple[str, str]] = []

    async def get_active_by_address(self, *, platform: str, address: str):
        self.asked.append((platform, address))
        return self._by_address.get(address)


@pytest.mark.asyncio
async def test_a_later_recipient_matches_and_is_stamped_as_the_address() -> None:
    surface = SimpleNamespace(id="s1")
    repository = _Repository({"pod@example.test": surface})
    normalized: dict = {"to": "alias@example.test"}

    found = await surface_for_recipients(
        repository, normalized, ["alias@example.test", "pod@example.test"]
    )

    assert found is surface
    assert normalized["to"] == "pod@example.test"
    assert repository.asked == [
        ("RESEND", "alias@example.test"),
        ("RESEND", "pod@example.test"),
    ]


@pytest.mark.asyncio
async def test_no_matching_recipient_leaves_the_payload_alone() -> None:
    normalized: dict = {"to": "alias@example.test"}

    found = await surface_for_recipients(
        _Repository({}), normalized, ["alias@example.test"]
    )

    assert found is None
    assert normalized["to"] == "alias@example.test"
