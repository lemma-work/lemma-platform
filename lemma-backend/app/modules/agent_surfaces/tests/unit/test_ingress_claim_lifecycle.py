"""A delivery claim is kept only for a message that is actually handed on.

Ingress spends a Redis claim on every live message so a redelivery is not run
twice. The claim used to be spent before the work it guards, and only a failed
enqueue handed it back: a failure in sender resolution, routing, binding the
conversation or the commit left it spent, so the inbox's retry read the message
as a duplicate and dropped it -- and a `DomainError` is marked terminal, so it
was never retried at all. Either way the person sent a message and nothing
happened, with no error anywhere.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.models import SurfaceSenderProfile
from app.modules.agent_surfaces.domain.onboarding_state import OnboardingIngressResult
from app.modules.agent_surfaces.events import handlers
from app.modules.agent_surfaces.services.fallback_reply_service import (
    deliver_fallback_reply,
    prepare_unrouted_context,
)
from app.modules.agent_surfaces.services.onboarding_sender import personal_dm_result
from app.modules.agent_surfaces.services.personal_dm_routes import (
    PersonalRouteUnavailable,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _conversation,
    _slack_event,
    _slack_surface,
    build_ingress_service,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


def _slack_request() -> SurfacePlatformWebhookIngress:
    return SurfacePlatformWebhookIngress(source="slack", payload={}, headers={})


def _member_service(*, conversation_error: Exception | None = None):
    """A Slack DM from a known member, ready to become a chat context."""
    surface = _slack_surface()
    user_id = uuid4()
    adapter = AsyncMock()
    adapter.parse_inbound_event.return_value = _slack_event()
    adapter.fetch_sender_profile.return_value = SurfaceSenderProfile(
        external_user_id="U123", email="sender@example.com", display_name="Sender"
    )
    service = build_ingress_service(
        adapter=adapter,
        surfaces=[surface],
        resolved_user=ResolvedSurfaceUser(
            internal_user_id=user_id,
            external_user_id="U123",
            email="sender@example.com",
            display_name="Sender",
        ),
        conversation=_conversation(surface, user_id),
    )
    if conversation_error is not None:
        agent_conversations.open_surface_conversation.side_effect = conversation_error
    return service, surface


# --- 1. prepare_ingress ---------------------------------------------------


async def test_a_failure_after_the_claim_hands_the_claim_back():
    service, surface = _member_service(conversation_error=RuntimeError("db went away"))

    with pytest.raises(RuntimeError, match="db went away"):
        await service.prepare_ingress(_slack_request())

    service.event_dedup_store.claim_message.assert_awaited_once()
    service.event_dedup_store.release_message.assert_awaited_once()
    released = service.event_dedup_store.release_message.await_args.kwargs
    claimed = service.event_dedup_store.claim_message.await_args.kwargs
    assert released == claimed, "the release must name exactly the key that was claimed"
    assert released["surface_installation_id"] == surface.id


async def test_a_domain_error_after_the_claim_also_hands_it_back():
    """The terminal case: the inbox never retries it, so nobody else would."""
    from app.core.domain.errors import DomainError

    service, _ = _member_service(conversation_error=DomainError("nope"))

    with pytest.raises(DomainError):
        await service.prepare_ingress(_slack_request())

    service.event_dedup_store.release_message.assert_awaited_once()


async def test_a_prepared_context_keeps_its_claim():
    service, _ = _member_service()

    context = await service.prepare_ingress(_slack_request())

    assert context is not None
    service.event_dedup_store.release_message.assert_not_awaited()


async def test_a_duplicate_has_no_claim_of_its_own_to_give_back():
    service, _ = _member_service()
    service.event_dedup_store.claim_message.return_value = False

    assert await service.prepare_ingress(_slack_request()) is None

    service.event_dedup_store.release_message.assert_not_awaited()


# --- 1b. the unrouted fallback -------------------------------------------


def _unrouted_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.WHATSAPP,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_message_id="wamid.1",
        external_thread_id="4477",
        sender_external_user_id="4477",
        message_text="hi",
        is_dm=True,
    )


async def test_a_failure_building_the_unrouted_reply_hands_the_claim_back():
    adapter = SimpleNamespace(
        unresolved_sender_reply=lambda _event: (_ for _ in ()).throw(
            RuntimeError("adapter broke")
        ),
        linked_sender_confirmation=lambda _event: None,
    )
    store = SimpleNamespace(
        claim_message=AsyncMock(return_value=True), release_message=AsyncMock()
    )

    with pytest.raises(RuntimeError, match="adapter broke"):
        await prepare_unrouted_context(
            platform="WHATSAPP",
            surface=None,
            parsed=_unrouted_event(),
            adapter=adapter,
            resolved_user=ResolvedSurfaceUser(),
            agent_display_name="Lemma",
            event_dedup_store=store,
        )

    store.release_message.assert_awaited_once()
    assert store.release_message.await_args.kwargs["external_message_id"] == "wamid.1"


# --- 1c. the personal-DM claim in onboarding ------------------------------


async def _personal_dm(failure: Exception):
    installation_id = uuid4()
    store = SimpleNamespace(
        claim_message=AsyncMock(return_value=True), release_message=AsyncMock()
    )

    async def prepare():
        raise failure

    try:
        result = await personal_dm_result(
            store,
            installation_surface_id=installation_id,
            event=_unrouted_event(),
            prepare=prepare,
        )
    except Exception as escaped:
        return installation_id, store, escaped
    return installation_id, store, result


async def test_any_failure_preparing_a_personal_dm_hands_the_claim_back():
    installation_id, store, escaped = await _personal_dm(RuntimeError("commit failed"))

    assert isinstance(escaped, RuntimeError)
    store.release_message.assert_awaited_once()
    assert (
        store.release_message.await_args.kwargs["surface_installation_id"]
        == installation_id
    )


async def test_a_dead_personal_route_still_falls_through_and_hands_the_claim_back():
    _installation_id, store, result = await _personal_dm(
        PersonalRouteUnavailable("route died")
    )

    assert result.handled is False
    store.release_message.assert_awaited_once()


async def test_a_prepared_personal_dm_keeps_its_claim():
    store = SimpleNamespace(
        claim_message=AsyncMock(return_value=True), release_message=AsyncMock()
    )
    context = object()

    async def prepare():
        return context

    result = await personal_dm_result(
        store,
        installation_surface_id=uuid4(),
        event=_unrouted_event(),
        prepare=prepare,
    )

    assert result.handled is True and result.context is context
    store.release_message.assert_not_awaited()


# --- 2. batched deliveries ------------------------------------------------


def _reply_context() -> SurfaceReplyContext:
    return SurfaceReplyContext(
        platform=SurfacePlatform.TELEGRAM,
        event=ParsedInboundSurfaceEvent(
            platform=SurfacePlatform.TELEGRAM,
            conversation_type=ConversationType.EXTERNAL_DM,
            external_thread_id="123",
            sender_external_user_id="123",
            message_text="hi",
            is_dm=True,
            reply_target={"chat_id": "123"},
        ),
        reply_message="hello",
    )


@asynccontextmanager
async def _uow_factory():
    yield AsyncMock()


class _BatchedDelivery:
    """A delivery of several messages, each of which spends a claim.

    The onboarding handler is the injection point: answering "handled" with a
    context is what a prepared message looks like to the loop under test, and it
    records when each one was prepared.
    """

    def __init__(self, *, parts: int) -> None:
        self.parts = [_slack_request()] * parts
        self.order: list[str] = []
        self.store = SimpleNamespace(release_message=AsyncMock())
        self.told = AsyncMock()

    async def prepare(self, _request) -> OnboardingIngressResult:
        self.order.append("prepare")
        return OnboardingIngressResult(True, _reply_context())

    async def run(self, job_queue) -> None:
        await handlers._enqueue_deliveries(
            self.parts,
            _event(),
            onboarding_handler=self.prepare,
            uow_factory=_uow_factory,
            job_queue=job_queue,
            event_dedup_store=self.store,
            try_interaction=AsyncMock(return_value=False),
            tell_failed=self.told,
        )


def _event():
    from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent

    return SurfaceWebhookReceivedEvent(source="whatsapp", payload={"entry": []})


async def test_each_part_is_enqueued_before_the_next_is_prepared():
    delivery = _BatchedDelivery(parts=3)
    job_queue = AsyncMock()
    job_queue.enqueue.side_effect = lambda *a, **k: delivery.order.append("enqueue")

    await delivery.run(job_queue)

    assert delivery.order == ["prepare", "enqueue"] * 3


async def test_a_failed_part_frees_its_own_claim_and_does_not_stop_the_rest():
    """Part 2 fails to enqueue: its claim is handed back, part 3 still goes out,
    and the delivery still fails so the inbox retries -- which then finds parts
    1 and 3 duplicates and delivers only part 2."""
    delivery = _BatchedDelivery(parts=3)
    attempts = 0

    async def enqueue(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        delivery.order.append("enqueue")
        if attempts == 2:
            raise ConnectionError("redis blip")

    job_queue = AsyncMock()
    job_queue.enqueue.side_effect = enqueue

    with pytest.raises(ConnectionError):
        await delivery.run(job_queue)

    assert delivery.order == ["prepare", "enqueue"] * 3
    delivery.store.release_message.assert_awaited_once()


async def test_the_last_attempt_tells_the_sender_once():
    """Only the final attempt speaks: an earlier failure is retried, and usually
    goes through."""
    from app.core.infrastructure.events import inbox

    job_queue = AsyncMock()
    job_queue.enqueue.side_effect = ConnectionError("redis blip")

    for number, told in ((1, False), (3, True)):
        delivery = _BatchedDelivery(parts=1)
        token = inbox._inbox_attempt.set(
            inbox.InboxAttempt(number=number, max_attempts=3)
        )
        try:
            with pytest.raises(ConnectionError):
                await delivery.run(job_queue)
        finally:
            inbox._inbox_attempt.reset(token)
        assert delivery.told.await_count == int(told)


# --- 7. the stranger-reply window -----------------------------------------


def _fallback_context() -> SurfaceReplyContext:
    return SurfaceReplyContext(
        platform=SurfacePlatform.WHATSAPP,
        surface_id=uuid4(),
        reply_kind="signup",
        reply_message="Sign up to talk to this agent.",
        event=_unrouted_event(),
    )


def _window_store():
    return SimpleNamespace(
        claim_stranger_reply=AsyncMock(return_value=True),
        release_stranger_reply=AsyncMock(),
    )


async def test_a_failed_send_gives_the_stranger_window_back(caplog):
    adapter = SimpleNamespace(deliver=AsyncMock(side_effect=RuntimeError("boom")))
    store = _window_store()
    context = _fallback_context()

    with caplog.at_level(logging.WARNING):
        await deliver_fallback_reply(
            adapter=adapter,
            context=context,
            credentials={"access_token": "token"},
            event_dedup_store=store,
        )

    store.release_stranger_reply.assert_awaited_once_with(
        platform=str(context.platform),
        surface_installation_id=context.surface_id,
        sender_external_user_id="4477",
    )


async def test_a_delivered_reply_keeps_the_window():
    adapter = SimpleNamespace(deliver=AsyncMock(return_value=None))
    store = _window_store()

    await deliver_fallback_reply(
        adapter=adapter,
        context=_fallback_context(),
        credentials={"access_token": "token"},
        event_dedup_store=store,
    )

    store.release_stranger_reply.assert_not_awaited()


async def test_the_redis_store_releases_the_key_it_claims(monkeypatch):
    """The two halves of the window have to name the same key."""
    from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
        RedisSurfaceEventDedupStore,
    )

    store = RedisSurfaceEventDedupStore(redis_url="redis://unused")
    redis = SimpleNamespace(set=AsyncMock(return_value=True), delete=AsyncMock())
    store._redis = redis
    surface_id = uuid4()

    await store.claim_stranger_reply(
        platform="WHATSAPP",
        surface_installation_id=surface_id,
        sender_external_user_id="4477",
    )
    await store.release_stranger_reply(
        platform="WHATSAPP",
        surface_installation_id=surface_id,
        sender_external_user_id="4477",
    )

    assert redis.delete.await_args.args[0] == redis.set.await_args.args[0]
