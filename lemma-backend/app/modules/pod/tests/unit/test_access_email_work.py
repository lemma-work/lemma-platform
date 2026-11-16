"""Which access changes write a letter to the person whose access changed."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.pod.domain.events import PodMemberAddedEvent
from app.modules.pod.events import access_email_work


def _added(*, user_id, added_by_user_id) -> PodMemberAddedEvent:
    return PodMemberAddedEvent(
        pod_id=uuid4(),
        user_id=user_id,
        role="POD_USER",
        email="jane@acme.com",
        added_by_user_id=added_by_user_id,
    )


@pytest.fixture
def writer(monkeypatch) -> AsyncMock:
    add = AsyncMock()
    monkeypatch.setattr(
        access_email_work.WorkWriter, "of", MagicMock(return_value=MagicMock(add=add))
    )
    return add


@pytest.mark.asyncio
async def test_someone_else_adding_you_writes_a_letter(writer: AsyncMock):
    user_id, adder = uuid4(), uuid4()

    await access_email_work.add_member_added_email_work(
        MagicMock(), _added(user_id=user_id, added_by_user_id=adder)
    )

    writer.assert_awaited_once()
    kind, _ns, payload = writer.await_args.args
    assert kind == access_email_work.POD_MEMBER_ADDED_EMAIL
    assert payload.user_id == user_id
    assert payload.added_by_user_id == adder


@pytest.mark.asyncio
async def test_an_accepted_invitation_writes_none(writer: AsyncMock):
    """They already said yes in the invitation; nobody added them."""
    await access_email_work.add_member_added_email_work(
        MagicMock(), _added(user_id=uuid4(), added_by_user_id=None)
    )

    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_adding_yourself_writes_none(writer: AsyncMock):
    me = uuid4()

    await access_email_work.add_member_added_email_work(
        MagicMock(), _added(user_id=me, added_by_user_id=me)
    )

    writer.assert_not_awaited()
