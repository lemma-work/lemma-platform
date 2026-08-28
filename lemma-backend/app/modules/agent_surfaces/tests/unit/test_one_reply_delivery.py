"""A surface that gets one reply, and what a run may put in it.

`display_resource` on an email surface used to return
`success=True, "FILE resource ready for display."` and deliver nothing. The
model believed it had shown the file; the recipient never saw one. These pin
both halves of the fix -- the file is held, and the reply carries it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.tools.user_interaction.models import (
    DisplayResourceRequest,
    DisplayResourceResponse,
    DisplayResourceType,
)
from app.modules.agent.tools.user_interaction.pydantic_adapter import (
    _maybe_deliver_to_surface,
)
from app.modules.agent_surfaces.platforms.email_attachments import (
    outbound_paths_for_reply,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
    DeliveryCardinality,
)
from app.modules.agent_surfaces.services.pending_envelope import (
    discard_display_paths,
    remember_display_path,
    take_display_paths,
)

pytestmark = pytest.mark.unit


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


def test_a_file_shown_twice_is_attached_once() -> None:
    conversation = uuid4()
    assert remember_display_path(conversation, "/me/q3.pdf")
    assert remember_display_path(conversation, "/me/q3.pdf")
    assert take_display_paths(conversation) == ["/me/q3.pdf"]


def test_draining_is_final_so_a_second_reply_does_not_re_attach() -> None:
    conversation = uuid4()
    remember_display_path(conversation, "/me/q3.pdf")
    assert take_display_paths(conversation) == ["/me/q3.pdf"]
    assert take_display_paths(conversation) == []


def test_a_runaway_run_is_bounded_rather_than_growing_forever() -> None:
    conversation = uuid4()
    accepted = [remember_display_path(conversation, f"/me/{i}.pdf") for i in range(40)]
    assert accepted.count(True) == 20
    assert accepted[-1] is False
    discard_display_paths(conversation)


def test_the_reply_carries_what_the_agent_asked_for_and_what_it_showed() -> None:
    conversation = uuid4()
    remember_display_path(conversation, "/me/shown.pdf")
    deps = SimpleNamespace(conversation_id=conversation)
    assert outbound_paths_for_reply(deps, ["/me/asked.csv"]) == [
        "/me/asked.csv",
        "/me/shown.pdf",
    ]


def test_a_file_both_asked_for_and_shown_is_one_attachment() -> None:
    conversation = uuid4()
    remember_display_path(conversation, "/me/q3.pdf")
    deps = SimpleNamespace(conversation_id=conversation)
    assert outbound_paths_for_reply(deps, ["/me/q3.pdf"]) == ["/me/q3.pdf"]


# --- the tool's own answer ------------------------------------------------


async def _display(request: DisplayResourceRequest, *, platform: str, conversation):
    response = DisplayResourceResponse(success=True, message="ready")
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            surface_platform=platform, conversation_id=conversation, pod_id=uuid4()
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
    assert take_display_paths(conversation) == ["/me/q3.pdf"]


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
    assert take_display_paths(conversation) == []


async def test_a_chat_surface_is_untouched_by_any_of_this() -> None:
    conversation = uuid4()
    from unittest.mock import patch

    with patch(
        "app.composition.agent_surface_runtime.deliver_display_resource",
        new=AsyncMock(return_value=True),
    ) as delivered:
        response = await _display(
            DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/q3.pdf"),
            platform="SLACK",
            conversation=conversation,
        )
    assert response.success is True
    delivered.assert_awaited_once()
    assert take_display_paths(conversation) == [], "chat delivers now, it does not hold"


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
