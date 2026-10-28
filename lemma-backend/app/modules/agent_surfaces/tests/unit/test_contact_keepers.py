"""A turn continues a conversation only if it answers the same person.

A conversation is found by a key -- a routing link, a web chat's session -- and
the key says nothing about whom the conversation answers. A contact's turn that
continued one naming nobody would run with the keeper's authority; a member's
that continued a contact's would run as the contact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.contracts.channels import SurfaceConversation
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent_surfaces.services.contact_keepers import owned_or_moved

pytestmark = pytest.mark.asyncio

KEEPER = uuid4()


def _conversation(audience: Audience) -> SurfaceConversation:
    return SurfaceConversation(
        id=uuid4(),
        user_id=KEEPER,
        pod_id=uuid4(),
        agent_id=None,
        title=None,
        updated_at=datetime.now(timezone.utc),
        audience=audience,
    )


async def test_a_contacts_turn_continues_their_own_conversation():
    contact = uuid4()

    assert await owned_or_moved(
        None,  # type: ignore[arg-type]
        _conversation(Audience.contact(contact)),
        KEEPER,
        contact_id=contact,
    )


async def test_a_contacts_turn_does_not_continue_one_that_names_nobody():
    """A conversation from before contacts were a column names nobody, and
    would answer the contact with the keeper's own authority."""
    assert not await owned_or_moved(
        None,  # type: ignore[arg-type]
        _conversation(Audience.member()),
        KEEPER,
        contact_id=uuid4(),
    )


async def test_a_contacts_turn_does_not_continue_another_contacts():
    """Unless the contact it names became this one -- asked of the contacts,
    which here know of nobody."""
    nobody = SimpleNamespace(
        session=SimpleNamespace(scalar=AsyncMock(return_value=None))
    )
    assert not await owned_or_moved(
        nobody,  # type: ignore[arg-type]
        _conversation(Audience.contact(uuid4())),
        KEEPER,
        contact_id=uuid4(),
    )


async def test_a_members_turn_does_not_continue_a_contacts():
    assert not await owned_or_moved(
        None,  # type: ignore[arg-type]
        _conversation(Audience.contact(uuid4())),
        KEEPER,
        contact_id=None,
    )


async def test_a_members_turn_continues_their_own():
    assert await owned_or_moved(
        None,  # type: ignore[arg-type]
        _conversation(Audience.member()),
        KEEPER,
        contact_id=None,
    )
