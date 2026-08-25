"""One ladder, every part, every platform.

`deliver` is the seam eighteen verbs used to be. What these pin is the thing
that was previously written out by hand at each call site and therefore drifted:
native first, then the part's own text, then say it reached nobody.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.envelope import (
    EnvelopeFile,
    EnvelopeVoice,
    PartDelivery,
    SurfaceEnvelope,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
from app.modules.agent_surfaces.domain.models import (
    APPROVAL_DECISION_APPROVE,
    APPROVAL_DECISION_DENY,
    SurfaceApprovalButton,
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionOption,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter

pytestmark = pytest.mark.unit


class _Adapter(BaseSurfaceAdapter):
    """A platform that can be told what it supports, one verb at a time."""

    platform = "TEST"

    def __init__(self, **verbs: Any) -> None:
        self.send_message = verbs.get("send_message", AsyncMock())
        self._render_choices = verbs.get("_render_choices", AsyncMock(return_value=False))
        self._render_decision = verbs.get("_render_decision", AsyncMock(return_value=False))
        self._render_resource = verbs.get("_render_resource", AsyncMock())
        self._render_file = verbs.get(
            "_render_file", AsyncMock(return_value=False)
        )
        self._render_voice = verbs.get(
            "_render_voice", AsyncMock(return_value=False)
        )


def _event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="thread-1",
        message_text="hi",
    )


async def _deliver(adapter: _Adapter, envelope: SurfaceEnvelope):
    return await adapter.deliver(credentials={}, event=_event(), envelope=envelope)


def _questions() -> SurfaceQuestionRenderPlan:
    return SurfaceQuestionRenderPlan(
        title="Pick",
        callback_id="conv|tool",
        questions=[
            SurfaceQuestion(
                header="color",
                question="Which colour?",
                options=[SurfaceQuestionOption(label="Red")],
            )
        ],
    )


def _decision() -> SurfaceApprovalRenderPlan:
    return SurfaceApprovalRenderPlan(
        title="Delete order 42",
        callback_id="conv|tool",
        buttons=[
            SurfaceApprovalButton(label="Approve", decision=APPROVAL_DECISION_APPROVE),
            SurfaceApprovalButton(label="Deny", decision=APPROVAL_DECISION_DENY),
        ],
    )


# --- the ladder -----------------------------------------------------------


async def test_a_native_platform_renders_natively_and_says_nothing_extra() -> None:
    adapter = _Adapter(_render_choices=AsyncMock(return_value=True))
    receipt = await _deliver(adapter, SurfaceEnvelope(choices=_questions()))
    assert receipt.parts["choices"] is PartDelivery.NATIVE
    adapter.send_message.assert_not_awaited()


async def test_a_platform_without_native_choices_still_asks_the_question() -> None:
    """The promise: never dropped for lack of native support."""
    adapter = _Adapter()
    receipt = await _deliver(adapter, SurfaceEnvelope(choices=_questions()))
    assert receipt.parts["choices"] is PartDelivery.DEGRADED
    assert "Which colour?" in adapter.send_message.await_args.kwargs["message"]


async def test_a_native_render_that_raises_falls_back_rather_than_propagating() -> None:
    adapter = _Adapter(
        _render_decision=AsyncMock(side_effect=httpx.ConnectError("no route"))
    )
    receipt = await _deliver(adapter, SurfaceEnvelope(decision=_decision()))
    assert receipt.parts["decision"] is PartDelivery.DEGRADED
    assert "approve" in adapter.send_message.await_args.kwargs["message"].lower()


async def test_a_part_that_reaches_nobody_is_recorded_as_such() -> None:
    adapter = _Adapter(
        send_message=AsyncMock(side_effect=httpx.ConnectError("no route")),
    )
    with pytest.raises(AgentSurfacePlatformError):
        await _deliver(adapter, SurfaceEnvelope(choices=_questions()))


async def test_an_envelope_where_something_landed_does_not_raise() -> None:
    """Partial delivery is a receipt, not an exception; only total failure raises."""
    adapter = _Adapter(
        _render_file=AsyncMock(return_value=False),
        send_message=AsyncMock(),
    )
    receipt = await _deliver(
        adapter,
        SurfaceEnvelope(
            text="Here is the report.",
            files=[
                EnvelopeFile(
                    file_name="q3.pdf", content=b"%PDF", mime_type="application/pdf"
                )
            ],
        ),
    )
    assert receipt.parts["text"] is PartDelivery.NATIVE
    assert receipt.parts["files"] is PartDelivery.DEGRADED
    assert receipt.delivered


# --- what a bug must not look like ---------------------------------------


async def test_a_bug_in_our_own_code_crashes_instead_of_degrading() -> None:
    """The reason the catch is an enumerated tuple and not `except Exception`.

    A TypeError from a signature that drifted is not "the platform said no". It
    is exactly the class of thing a broad catch turned into silent degradation,
    which is how stream_progress was dead on two platforms for a release.
    """
    adapter = _Adapter(
        _render_choices=AsyncMock(side_effect=TypeError("unexpected keyword"))
    )
    with pytest.raises(TypeError):
        await _deliver(adapter, SurfaceEnvelope(choices=_questions()))


async def test_an_empty_envelope_is_a_caller_bug_and_says_so() -> None:
    with pytest.raises(AgentSurfacePlatformError):
        await _deliver(_Adapter(), SurfaceEnvelope())


# --- ordering and per-part behaviour --------------------------------------


async def test_the_lead_in_is_delivered_before_the_thing_it_leads_into() -> None:
    """Narration and the question are one envelope, so their order is decided here."""
    calls: list[str] = []
    adapter = _Adapter(
        send_message=AsyncMock(side_effect=lambda **_: calls.append("text")),
        _render_choices=AsyncMock(
            side_effect=lambda **_: calls.append("choices") or True
        ),
    )
    await _deliver(
        adapter, SurfaceEnvelope(text="I need one thing first.", choices=_questions())
    )
    assert calls == ["text", "choices"]


async def test_voice_degrades_to_the_same_bytes_as_a_file_not_to_a_mention() -> None:
    """A platform with no voice notes still has an audio player."""
    adapter = _Adapter(
        _render_voice=AsyncMock(return_value=False),
        _render_file=AsyncMock(return_value=True),
    )
    receipt = await _deliver(
        adapter,
        SurfaceEnvelope(
            voice=EnvelopeVoice(
                file_name="reply.ogg", content=b"OggS", mime_type="audio/ogg"
            )
        ),
    )
    assert receipt.parts["voice"] is PartDelivery.NATIVE
    assert adapter._render_file.await_args.kwargs["file_bytes"] == b"OggS"


async def test_a_file_that_cannot_be_attached_degrades_to_its_link_card() -> None:
    """The second rung is the same card the resource part renders, not a line of text."""
    adapter = _Adapter(_render_resource=AsyncMock())
    receipt = await _deliver(
        adapter,
        SurfaceEnvelope(
            files=[
                EnvelopeFile(
                    file_name="q3.pdf",
                    content=b"%PDF",
                    mime_type="application/pdf",
                    caption="Q3 revenue, down 4%.",
                    fallback=SurfaceDisplayRenderPlan(
                        resource_type="FILE", title="q3.pdf"
                    ),
                )
            ]
        ),
    )
    assert receipt.parts["files"] is PartDelivery.DEGRADED
    assert (
        adapter._render_resource.await_args.kwargs["render_plan"].title == "q3.pdf"
    )


async def test_a_file_with_no_card_to_fall_back_on_still_says_what_it_was() -> None:
    adapter = _Adapter()
    await _deliver(
        adapter,
        SurfaceEnvelope(
            files=[
                EnvelopeFile(
                    file_name="q3.pdf",
                    content=b"%PDF",
                    mime_type="application/pdf",
                    caption="Q3 revenue, down 4%.",
                )
            ]
        ),
    )
    assert "Q3 revenue, down 4%." in adapter.send_message.await_args.kwargs["message"]


async def test_a_resource_degrades_to_its_own_text() -> None:
    adapter = _Adapter(
        _render_resource=AsyncMock(side_effect=httpx.ConnectError("no route"))
    )
    receipt = await _deliver(
        adapter,
        SurfaceEnvelope(
            resources=[SurfaceDisplayRenderPlan(resource_type="TABLE", title="Orders")]
        ),
    )
    assert receipt.parts["resources"] is PartDelivery.DEGRADED
    assert "Orders" in adapter.send_message.await_args.kwargs["message"]
