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
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata
from app.modules.agent_surfaces.domain.whatsapp_numbers import WhatsAppNumberEntity
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    AttachmentIngest,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _conversation,
    build_turn_starter,
    conversation_operations,  # noqa: F401  (autouse fixture)
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


class _Numbers:
    """The pool read, answered from a dict, recording which numbers were asked."""

    def __init__(self, rows: dict[str, WhatsAppNumberEntity]) -> None:
        self.rows = rows
        self.seen: list[str] = []

    async def get_by_phone_number_id(self, phone_number_id: str):
        self.seen.append(phone_number_id)
        return self.rows.get(phone_number_id)


def _resolver(rows: dict[str, WhatsAppNumberEntity]):
    """The resolver with the pool read answered from a dict.

    Handed over rather than reached into: the subject is which number the
    resolver decides to read, and `seen` is how these say so.
    """
    numbers = _Numbers(rows)
    resolver = SurfaceCredentialResolver(uow=SimpleNamespace(), pooled_numbers=numbers)
    return resolver, numbers.seen


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


async def test_the_platform_lookup_answers_from_the_number_the_message_arrived_on():
    """The worker has no surface row in hand, only the run's context.

    `for_platform` with no surface returned the settings answer outright, so
    everything the worker did to an inbound message used the settings token.
    """
    resolver, seen = _resolver({_POOLED: _pooled_row()})

    credentials = await resolver.for_platform(
        SurfacePlatform.WHATSAPP, None, surface=None, arrived_on=_POOLED
    )

    assert credentials["access_token"] == "token-for-b"
    assert credentials["phone_number_id"] == _POOLED
    assert seen == [_POOLED]


async def test_the_platform_lookup_reads_no_pool_for_another_platform():
    resolver, seen = _resolver({_POOLED: _pooled_row()})

    await resolver.for_platform(
        SurfacePlatform.TELEGRAM, None, surface=None, arrived_on=_POOLED
    )

    assert seen == []


def _whatsapp_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="whatsapp-shared-line",
        surface_type=SurfacePlatform.WHATSAPP,
        config=SurfaceConfig(),
        is_active=True,
    )


async def test_the_read_receipt_and_media_download_use_the_arriving_numbers_token():
    """Marking read, showing typing and fetching media act on *that* message.

    Replies already went out from the pooled number; these three used the
    settings token, which belongs to another number and, under another WABA,
    Meta refuses -- so the person saw no ticks, no typing, and the agent never
    received the photo.
    """
    surface = _whatsapp_surface()
    conversation = _conversation(surface, uuid4())
    adapter = AsyncMock()
    ingest = SimpleNamespace(
        ingest_attachments=AsyncMock(return_value=AttachmentIngest())
    )
    starter = build_turn_starter(
        adapter=adapter,
        surfaces=[surface],
        conversation=conversation,
        file_ingest_service=ingest,
        pooled_numbers=_Numbers({_POOLED: _pooled_row()}),
    )
    context = SurfaceChatContext(
        platform="WHATSAPP",
        pod_id=surface.pod_id,
        agent_name=None,
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        surface_id=surface.id,
        surface_config=surface.config,
        agent_display_name="Lemma",
        message_text="hello",
        message_metadata=SurfaceMessageMetadata(surface_platform="WHATSAPP"),
        message_user_id=conversation.user_id,
        message_external_user_id="14155550000",
        event=_event(_POOLED),
    )

    await starter.execute_chat(context)

    indicator = adapter.add_processing_indicator.await_args.kwargs["credentials"]
    assert indicator["access_token"] == "token-for-b"
    assert indicator["phone_number_id"] == _POOLED
    media = ingest.ingest_attachments.await_args.kwargs["credentials"]
    assert media["access_token"] == "token-for-b"


async def test_the_fallback_reply_goes_out_with_the_arriving_numbers_token():
    adapter = AsyncMock()
    starter = build_turn_starter(
        adapter=adapter, pooled_numbers=_Numbers({_POOLED: _pooled_row()})
    )
    context = SurfaceReplyContext(
        platform="WHATSAPP",
        reply_kind="signup",
        reply_message="Please sign up",
        event=_event(_POOLED),
    )

    await starter.execute_chat(context)

    sent = adapter.deliver.await_args.kwargs["credentials"]
    assert sent["access_token"] == "token-for-b"
    assert sent["phone_number_id"] == _POOLED
