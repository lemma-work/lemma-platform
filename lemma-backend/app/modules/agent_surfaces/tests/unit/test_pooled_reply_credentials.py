"""Which number a reply goes out from, when the surface does not hold one.

Every chat signup lands on the shared-line surface `_ensure_shared_surface`
mints, and that surface deliberately carries no `surface_identity_id`: several
personal pods in one organisation ride one line, and stamping the number would
put them under `uq_agent_org_whatsapp_number` and refuse the second person in a
domain-join organisation to sign up.

So the surface cannot say which number to answer from, and the settings number
answered all of them -- somebody who wrote to a pooled number got a reply from a
different one, and where that number sits under another WABA the settings token
is not authorised to send as it at all. The message being answered is what
knows, and these pin that it is what decides.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.whatsapp_numbers import WhatsAppNumberEntity
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)

pytestmark = pytest.mark.asyncio

_POOLED = "pooled-number-b"
_SETTINGS = "settings-number-a"


def _surface(identity: str | None):
    return SimpleNamespace(
        id=uuid4(),
        surface_type=SurfacePlatform.WHATSAPP,
        surface_identity_id=identity,
        account_id=None,
    )


def _event(phone_number_id: str | None):
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.WHATSAPP,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="wa-thread",
        sender_external_user_id="14155550000",
        message_text="hello",
        is_dm=True,
        reply_target=({"phone_number_id": phone_number_id} if phone_number_id else {}),
    )


def _resolver(rows: dict[str, WhatsAppNumberEntity]):
    """The resolver with the pool read answered from a dict.

    Handed over rather than reached into: the subject is which number the
    resolver decides to read, and `seen` is how these say so.
    """
    seen: list[str] = []

    class _Numbers:
        async def get_by_phone_number_id(self, phone_number_id: str):
            seen.append(phone_number_id)
            return rows.get(phone_number_id)

    resolver = SurfaceCredentialResolver(
        uow=SimpleNamespace(), pooled_numbers=_Numbers()
    )
    return resolver, seen


def _pooled_row() -> WhatsAppNumberEntity:
    return WhatsAppNumberEntity(
        phone_number_id=_POOLED,
        display_phone_number="+15551230002",
        waba_id="waba-b",
        access_token="token-for-b",
        app_secret="secret-b",
    )


async def test_a_shared_line_answers_from_the_number_the_message_arrived_on():
    """The bug this exists for: a signup on number B answered from number A."""
    resolver, seen = _resolver({_POOLED: _pooled_row()})

    credentials = await resolver.for_surface(_surface(None), arrived_on=_POOLED)

    assert credentials["phone_number_id"] == _POOLED
    assert credentials["access_token"] == "token-for-b"
    assert seen == [_POOLED]


async def test_a_surface_that_holds_a_number_keeps_it():
    """An allocated number is the surface's own and outranks the inbound one.

    It has to: a message the agent starts has no inbound event to have arrived
    on, so the surface is the only thing that can answer for those, and a reply
    that changed number depending on who spoke first would be worse than either.
    """
    held = WhatsAppNumberEntity(
        phone_number_id="held-number-c",
        display_phone_number="+15551230003",
        waba_id="waba-c",
        access_token="token-for-c",
    )
    resolver, seen = _resolver({_POOLED: _pooled_row(), "held-number-c": held})

    credentials = await resolver.for_surface(
        _surface("held-number-c"), arrived_on=_POOLED
    )

    assert credentials["access_token"] == "token-for-c"
    assert seen == ["held-number-c"]


async def test_a_number_with_no_row_leaves_the_settings_answer_standing():
    """A single-number deployment declares no rows, and must keep working.

    The one number is configured in settings exactly as it was before the pool
    existed, so an arriving number that matches nothing in the table has to fall
    through rather than blank the credentials.
    """
    resolver, seen = _resolver({})

    credentials = await resolver.for_surface(_surface(None), arrived_on=_SETTINGS)
    settings_answer = await resolver.for_surface(_surface(None))

    assert seen == [_SETTINGS]
    # Nothing laid over: the deployment-wide settings are the whole answer, as
    # they were before the pool existed.
    assert credentials == settings_answer


async def test_nothing_to_go_on_reads_nothing():
    """No held number and no inbound one is the settings answer, with no read."""
    resolver, seen = _resolver({_POOLED: _pooled_row()})

    await resolver.for_surface(_surface(None), arrived_on=None)

    assert seen == []
