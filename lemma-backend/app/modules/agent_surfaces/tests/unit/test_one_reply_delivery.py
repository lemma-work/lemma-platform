"""A surface that gets one reply, and what a run may put in it.

`display_resource` on an email surface used to return
`success=True, "FILE resource ready for display."` and deliver nothing. The
model believed it had shown the file; the recipient never saw one. These pin
both halves of the fix -- the file is held, and the reply carries it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache

from app.modules.agent.tools.user_interaction.models import (
    DisplayResourceRequest,
    DisplayResourceResponse,
    DisplayResourceType,
)
from app.modules.agent.tools.user_interaction.pydantic_adapter import (
    _maybe_deliver_to_surface,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
    DeliveryCardinality,
)
from app.modules.agent_surfaces.services import pending_envelope
from app.modules.agent_surfaces.services.pending_envelope import (
    RunFiles,
    discard_display_paths,
    held_display_paths,
    release_display_paths,
    remember_display_path,
)

pytestmark = pytest.mark.unit

RUN = RunFiles(uuid4())


@pytest.fixture(autouse=True)
def _held_paths_in_redis():
    """Held paths live in Redis now, so the tests need one that is not the network."""
    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="test:held", ttl_seconds=60
    )
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    previous = pending_envelope._cache
    pending_envelope._cache = cache
    yield cache
    pending_envelope._cache = previous


# --- the capability -------------------------------------------------------


def test_email_gets_one_delivery_and_chat_gets_many() -> None:
    for platform in ("RESEND",):
        caps = PLATFORM_CAPABILITIES[platform]
        assert caps.delivery_cardinality is DeliveryCardinality.ONE
    for platform in ("SLACK", "TEAMS", "TELEGRAM", "WHATSAPP"):
        caps = PLATFORM_CAPABILITIES[platform]
        assert caps.delivery_cardinality is DeliveryCardinality.MANY


def test_pausing_is_not_a_capability_because_every_surface_can() -> None:
    """`can_pause_for_a_person` was added, then removed two commits later.

    It was meant to separate "delivers once" from "cannot hold a run open", and
    the second turned out not to exist: a pause ends the run and resumes on the
    answer, which email does as well as chat. A field with one answer is not a
    capability, it is a constant.
    """
    caps = PLATFORM_CAPABILITIES["RESEND"]
    assert not hasattr(caps, "can_pause_for_a_person")
    assert caps.delivery_cardinality is DeliveryCardinality.ONE


async def test_a_file_shown_twice_is_attached_once() -> None:
    conversation = uuid4()
    assert await remember_display_path(conversation, RUN, "/me/q3.pdf")
    assert await remember_display_path(conversation, RUN, "/me/q3.pdf")
    assert await held_display_paths(conversation, RUN) == ["/me/q3.pdf"]


async def test_reading_the_held_files_does_not_drain_them() -> None:
    """A reply that fails to send must not take the attachments with it.

    They used to be popped on read, so a send that then failed had spent the
    files, and the retry -- or the error email -- went out without them.
    """
    conversation = uuid4()
    await remember_display_path(conversation, RUN, "/me/q3.pdf")
    assert await held_display_paths(conversation, RUN) == ["/me/q3.pdf"]
    assert await held_display_paths(conversation, RUN) == ["/me/q3.pdf"]


async def test_releasing_is_final_so_a_second_reply_does_not_re_attach() -> None:
    conversation = uuid4()
    await remember_display_path(conversation, RUN, "/me/q3.pdf")
    await release_display_paths(conversation, RUN, ["/me/q3.pdf"])
    assert await held_display_paths(conversation, RUN) == []


async def test_a_file_shown_while_the_reply_was_sending_is_not_released_with_it() -> (
    None
):
    conversation = uuid4()
    await remember_display_path(conversation, RUN, "/me/first.pdf")
    sent = await held_display_paths(conversation, RUN)
    await remember_display_path(conversation, RUN, "/me/second.pdf")
    await release_display_paths(conversation, RUN, sent)
    assert await held_display_paths(conversation, RUN) == ["/me/second.pdf"]


async def test_a_runaway_run_is_bounded_rather_than_growing_forever() -> None:
    conversation = uuid4()
    accepted = [
        await remember_display_path(conversation, RUN, f"/me/{i}.pdf")
        for i in range(40)
    ]
    assert accepted.count(True) == 20
    assert accepted[-1] is False
    await discard_display_paths(conversation, RUN)
    assert await held_display_paths(conversation, RUN) == []


async def test_two_files_shown_in_one_turn_are_both_held() -> None:
    """Parallel tool calls add to the same run's files at once.

    A read-modify-write of one list lost the second writer's file, and an
    attachment the model had been told was queued silently never went out.
    """
    conversation = uuid4()
    paths = [f"/me/{i}.pdf" for i in range(8)]
    results = await asyncio.gather(
        *(remember_display_path(conversation, RUN, path) for path in paths)
    )
    assert all(results)
    assert sorted(await held_display_paths(conversation, RUN)) == sorted(paths)


async def test_held_files_are_visible_to_a_different_process(
    _held_paths_in_redis: RedisJsonCache,
) -> None:
    """The reason they moved out of a dict.

    A remote harness's tool call executes in an API replica; the observer that
    sends the reply runs in a worker. Two caches over one Redis stand for the two
    processes -- a per-process dict would have shown the second one nothing.
    """
    conversation = uuid4()
    await remember_display_path(conversation, RUN, "/me/q3.pdf")

    other_process = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="test:held", ttl_seconds=60
    )
    other_process._redis = _held_paths_in_redis._redis
    pending_envelope._cache = other_process
    assert await held_display_paths(conversation, RUN) == ["/me/q3.pdf"]


async def test_redis_being_down_declines_the_file_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, _held_paths_in_redis: RedisJsonCache
) -> None:
    async def down(*_args, **_kwargs):
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(_held_paths_in_redis, "ordered_set_add", down)
    monkeypatch.setattr(_held_paths_in_redis, "ordered_set_members", down)
    conversation = uuid4()
    assert await remember_display_path(conversation, RUN, "/me/q3.pdf") is False
    # Reading degrades to "nothing held": the reply goes out without them.
    assert await held_display_paths(conversation, RUN) == []


# --- the tool's own answer ------------------------------------------------


async def _display(request: DisplayResourceRequest, *, platform: str, conversation):
    response = DisplayResourceResponse(success=True, message="ready")
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            surface_platform=platform,
            conversation_id=conversation,
            pod_id=uuid4(),
            agent_run_id=RUN.agent_run_id,
        ),
        tool_call_id="tool-1",
    )
    await _maybe_deliver_to_surface(ctx, request, response)
    return response


async def test_showing_a_file_on_email_holds_it_and_says_so() -> None:
    conversation = uuid4()
    response = await _display(
        DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/q3.pdf"),
        platform="RESEND",
        conversation=conversation,
    )
    assert response.success is True
    assert "attached to your email reply" in (response.message or "")
    assert await held_display_paths(conversation, RUN) == ["/me/q3.pdf"]


async def test_showing_a_file_on_email_reports_failure_when_it_cannot_be_held(
    monkeypatch: pytest.MonkeyPatch, _held_paths_in_redis: RedisJsonCache
) -> None:
    async def down(*_args, **_kwargs):
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(_held_paths_in_redis, "ordered_set_add", down)
    response = await _display(
        DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/q3.pdf"),
        platform="RESEND",
        conversation=uuid4(),
    )
    assert response.success is False
    assert response.message is None


async def test_showing_a_table_on_email_reports_failure_rather_than_success() -> None:
    """There is nothing to display in, and a false success is worse than a no.

    This is the exact shape of the original bug: the tool had already returned
    success before the email branch silently gave up.
    """
    conversation = uuid4()
    response = await _display(
        DisplayResourceRequest(type=DisplayResourceType.TABLE, name="orders"),
        platform="RESEND",
        conversation=conversation,
    )
    assert response.success is False
    assert "email conversation" in (response.error or "")
    assert await held_display_paths(conversation, RUN) == []


@pytest.mark.parametrize(
    ("platform", "delivered"), [("SLACK", True), ("TELEGRAM", False)]
)
async def test_a_chat_surface_delivers_now_and_says_what_became_of_it(
    platform: str, delivered: bool
) -> None:
    """A chat surface delivers at once and does not hold; and the tool reports it.

    The tool had already returned success before delivery was attempted, and
    `deliver_display_resource` answered False with the boolean dropped -- so the
    model told the person "here is the file" about a file that never arrived.
    The email branch already corrected `response`; this is the same correction
    for chat.
    """
    from unittest.mock import patch

    conversation = uuid4()
    with patch(
        "app.modules.agent_surfaces.contracts.egress.deliver_display_resource",
        new=AsyncMock(return_value=delivered),
    ) as deliver:
        response = await _display(
            DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/q3.pdf"),
            platform=platform,
            conversation=conversation,
        )

    deliver.assert_awaited_once()
    assert response.success is delivered
    if not delivered:
        assert response.message is None
        assert "could not be delivered" in (response.error or "")
    assert await held_display_paths(conversation, RUN) == [], (
        "chat delivers now, it does not hold"
    )


# --- the run stopping is the run stopping, whatever stopped it -------------


def _email_envelope(**parts):
    from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope

    return SurfaceEnvelope(**parts)


async def _render_one(envelope):
    """What a Resend adapter puts on the wire for one envelope."""
    from unittest.mock import AsyncMock

    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
    )
    from app.modules.agent_surfaces.platforms.resend.adapter import (
        ResendSurfaceAdapter,
    )

    adapter = ResendSurfaceAdapter()
    adapter.send_message = AsyncMock()  # type: ignore[method-assign]
    await adapter.deliver(
        credentials={},
        event=ParsedInboundSurfaceEvent(
            platform="RESEND",
            conversation_type=ConversationType.EXTERNAL_DM,
            external_thread_id="thread-1",
            message_text="hi",
        ),
        envelope=envelope,
    )
    return adapter.send_message.await_args


async def test_a_question_and_its_lead_in_are_one_email_not_two() -> None:
    """Two sends would be two emails, and email only gets one."""
    from app.modules.agent_surfaces.domain.models import (
        SurfaceQuestion,
        SurfaceQuestionOption,
        SurfaceQuestionRenderPlan,
    )

    call = await _render_one(
        _email_envelope(
            text="I found two candidates.",
            choices=SurfaceQuestionRenderPlan(
                title="Pick",
                callback_id="conv|tool",
                questions=[
                    SurfaceQuestion(
                        header="which",
                        question="Which one?",
                        options=[SurfaceQuestionOption(label="Red")],
                    )
                ],
            ),
        )
    )
    body = call.kwargs["message"]
    assert body.index("I found two candidates.") < body.index("Which one?"), (
        "the lead-in has to arrive above the question, not after it"
    )


async def test_an_approval_is_asked_in_the_reply_rather_than_suppressed() -> None:
    """Previously the tool refused on email, so the action ran unapproved or not
    at all. The prompt is text here, and a typed reply resolves it."""
    from app.modules.agent_surfaces.domain.models import (
        APPROVAL_DECISION_APPROVE,
        APPROVAL_DECISION_DENY,
        SurfaceApprovalButton,
        SurfaceApprovalRenderPlan,
    )

    call = await _render_one(
        _email_envelope(
            decision=SurfaceApprovalRenderPlan(
                title="Delete order 42",
                callback_id="conv|tool",
                buttons=[
                    SurfaceApprovalButton(
                        label="Approve", decision=APPROVAL_DECISION_APPROVE
                    ),
                    SurfaceApprovalButton(
                        label="Deny", decision=APPROVAL_DECISION_DENY
                    ),
                ],
            )
        )
    )
    body = call.kwargs["message"]
    assert "Delete order 42" in body
    assert '"approve"' in body and '"deny"' in body


async def test_a_failed_run_on_email_says_so_instead_of_vanishing() -> None:
    """It used to return early here, so the person's message simply disappeared
    and nothing distinguished that from never having been read."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.modules.agent_surfaces.services.progress_observer import (
        SurfaceAgentRunProgressObserver,
    )

    observer = SurfaceAgentRunProgressObserver.__new__(SurfaceAgentRunProgressObserver)
    observer._error_delivered = False
    observer._run_error_text = "I couldn't finish that request."
    sent: list[str] = []
    observer._send_agent_message = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda **kwargs: sent.append(kwargs["message"])
    )

    await observer._deliver_run_error(
        SimpleNamespace(id=uuid4(), metadata={"surface_platform": "RESEND"})
    )

    assert sent == ["I couldn't finish that request."]


# --- what a run says aloud, on a surface that only gets one reply ------------


async def test_audio_the_run_produced_rides_the_one_reply() -> None:
    """`say` on an email surface used to reach nobody and report success.

    `compose_one_reply` folded text, resources, choices and a decision, and the
    attachment list was built from `envelope.files` alone — so an envelope
    carrying only `voice`, which is exactly what `send_voice_note_for_conversation`
    builds, composed an empty body with no attachments and sent nothing at all.
    Email has no voice notes, so the bytes ride the reply as an attachment,
    which is the degradation `EnvelopeVoice` already documents.
    """
    from app.modules.agent_surfaces.domain.envelope import (
        EnvelopeVoice,
        SurfaceEnvelope,
    )

    call = await _render_one(
        SurfaceEnvelope(
            voice=EnvelopeVoice(
                file_name="answer.ogg",
                content=b"OggS-audio",
                mime_type="audio/ogg",
                caption="Here is what I found.",
            )
        )
    )
    assert call is not None, "the audio reached nobody"
    assert call.kwargs["metadata"]["attachments"] == [
        ("answer.ogg", b"OggS-audio", "audio/ogg")
    ]
    # A reply whose whole content is a sound file otherwise arrives blank.
    assert "Here is what I found." in call.kwargs["message"]


async def test_audio_is_recorded_as_degraded_not_as_a_voice_note() -> None:
    """An attachment a person opens is not a voice note that plays in-thread.

    The distinction is the only thing `receipt.degraded` is for.
    """
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
    )
    from app.modules.agent_surfaces.domain.envelope import (
        EnvelopeVoice,
        PartDelivery,
        SurfaceEnvelope,
    )
    from app.modules.agent_surfaces.platforms.resend.adapter import (
        ResendSurfaceAdapter,
    )

    adapter = ResendSurfaceAdapter()
    adapter.send_message = AsyncMock()  # type: ignore[method-assign]
    receipt = await adapter.deliver(
        credentials={},
        event=ParsedInboundSurfaceEvent(
            platform="RESEND",
            conversation_type=ConversationType.EXTERNAL_DM,
            external_thread_id="thread-1",
            message_text="hi",
        ),
        envelope=SurfaceEnvelope(
            voice=EnvelopeVoice(
                file_name="answer.ogg", content=b"OggS", mime_type="audio/ogg"
            )
        ),
    )
    assert receipt.parts["voice"] is PartDelivery.DEGRADED
    assert receipt.degraded == ["voice"]


async def test_a_one_reply_surface_that_reached_nobody_raises() -> None:
    """The check used to live inside the many-part path only.

    So a one-reply surface that sent nothing returned an empty receipt, and
    `_deliver_envelope` — which reads "no exception" as "delivered" — reported
    success for a run that had reached nobody.
    """
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
    )
    from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
    from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
    from app.modules.agent_surfaces.platforms.resend.adapter import (
        ResendSurfaceAdapter,
    )

    class _SendsNothing(ResendSurfaceAdapter):
        async def _render_one(self, **_: object):
            from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt

            return DeliveryReceipt(parts={})

    with pytest.raises(AgentSurfacePlatformError):
        await _SendsNothing().deliver(
            credentials={},
            event=ParsedInboundSurfaceEvent(
                platform="RESEND",
                conversation_type=ConversationType.EXTERNAL_DM,
                external_thread_id="thread-1",
                message_text="hi",
            ),
            envelope=SurfaceEnvelope(text="something"),
        )
