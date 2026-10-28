"""A run's answer on Slack finishes the message the run streamed into.

The run's observer streams the answer as the model writes it and, when the run
completed with an answer, leaves that stream open and *sealed* in its
``live_display`` record. The answer's channel delivery (``run_answer_sends``)
closes the same message with whatever the stream does not show yet, and
records that message's id -- it posts nothing. Anything else -- no record, a
stream that disagrees with the answer and was never sealed, a close the
platform refused -- takes the stream down and posts the answer.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import fakeredis
import pytest

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent.contracts import channel_replies
from app.modules.agent_surfaces.caches import LIVE_DISPLAYS, surface_caches
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt, PartDelivery
from app.modules.agent_surfaces.domain.models import (
    ProgressHandle,
    ProgressHandleKind,
    StreamAppendResult,
)
from app.modules.agent_surfaces.services import live_display
from app.modules.agent_surfaces.services import run_answer_sends
from app.modules.agent_surfaces.services.run_answer_sends import RunAnswerSends
from app.modules.agent_surfaces.services.live_display import (
    record_live_display,
    remainder_after,
    take_live_display,
)
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)
from app.modules.channels.contracts import SenderKind

pytestmark = pytest.mark.asyncio

STREAM = ProgressHandle(
    kind=ProgressHandleKind.STREAM, message_id="1700000001.000001", channel_id="D1"
)


def _run_ctx(conversation, **fields):
    """A run's context, naming the platform its conversation's surface records."""
    platform = (getattr(conversation, "metadata", None) or {}).get("surface_platform")
    return SimpleNamespace(surface_platform=platform, **fields)


@pytest.fixture(autouse=True)
def _displays_in_redis():
    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="test:live-display", ttl_seconds=60
    )
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    with surface_caches.substituted(LIVE_DISPLAYS, cache):
        yield cache


class _Progress:
    def __init__(self, egress: "_Egress") -> None:
        self.egress = egress

    async def finish_with_answer(self, **kwargs):
        self.egress.finished.append(kwargs)
        return self.egress.finish_result

    async def clear_progress(self, **kwargs):
        self.egress.cleared.append(kwargs)

    async def append_streamed_text(self, **kwargs):
        self.egress.appended.append(kwargs["text"])
        return StreamAppendResult(handle=STREAM, appended=True)

    async def show_typing(self, **kwargs):
        return False


class _Egress:
    def __init__(self, *, finish_result: bool = True) -> None:
        self.finished: list[dict] = []
        self.cleared: list[dict] = []
        self.appended: list[str] = []
        self.posted: list[dict] = []
        self.finish_result = finish_result
        self.progress = _Progress(self)

    async def deliver_message(self, **kwargs):
        self.posted.append(kwargs)
        return DeliveryReceipt(
            parts={"text": PartDelivery.NATIVE}, external_message_id="1700000009.1"
        )


class _Uow:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace(session=None, commit=_nothing)

    async def __aexit__(self, *exc):
        return None


@pytest.fixture(autouse=True)
def _a_completed_run(monkeypatch):
    """The run answered; the delivery's id notes go nowhere (no database)."""
    noted: list[str] = []

    async def note(_uow, _delivery_id, *, external_message_id):
        noted.append(external_message_id)

    monkeypatch.setattr(
        run_answer_sends, "run_reply_material", _material("COMPLETED", "answer")
    )
    monkeypatch.setattr(run_answer_sends, "delivery_noted", note)

    async def unnote(_uow, _delivery_id):
        noted.append(None)

    monkeypatch.setattr(run_answer_sends, "delivery_unnoted", unnote)
    monkeypatch.setattr(run_answer_sends, "discard_display_paths", _nothing)
    return noted


async def _nothing(*_args, **_kwargs) -> None:
    return None


async def _send(egress: _Egress, run_id, text: str):
    """The answer line's delivery to a Slack place, sent."""
    conversation_id = uuid4()
    line = SimpleNamespace(
        agent_run_id=run_id,
        conversation_id=conversation_id,
        sender=SimpleNamespace(kind=SenderKind.AGENT),
    )
    return await RunAnswerSends(_Uow(), egress_for=lambda _u: egress).send(
        delivery=SimpleNamespace(id=uuid4()),
        line=line,
        platform="SLACK",
        text=text,
        metadata={},
    )


# --- the arithmetic ------------------------------------------------------------


def test_the_remainder_is_what_the_stream_does_not_show_yet() -> None:
    assert remainder_after("", "Here is the answer.") == "Here is the answer."
    assert remainder_after("Here is the ", "Here is the answer.") == "answer."
    assert remainder_after("Here is the answer.", "Here is the answer.") == ""
    assert remainder_after("\nHere is", "Here is the answer.") == " the answer."
    assert remainder_after("Something else", "Here is the answer.") is None


async def test_the_reply_waits_for_the_observer_to_seal_the_stream() -> None:
    run_id, conversation_id = uuid4(), uuid4()
    await record_live_display(
        run_id, conversation_id=conversation_id, handle=STREAM, streamed="Here"
    )

    async def seal_later() -> None:
        await asyncio.sleep(0.3)
        await record_live_display(
            run_id,
            conversation_id=conversation_id,
            handle=STREAM,
            streamed="Here is it.",
            sealed=True,
        )

    sealer = asyncio.create_task(seal_later())
    display = await take_live_display(run_id, wait_seconds=5)
    await sealer
    assert display is not None and display.sealed
    assert display.streamed == "Here is it."


async def test_an_observer_that_never_seals_is_waited_for_only_so_long() -> None:
    run_id = uuid4()
    await record_live_display(
        run_id, conversation_id=uuid4(), handle=STREAM, streamed="Here"
    )
    display = await take_live_display(run_id, wait_seconds=0.3)
    assert display is not None and not display.sealed


# --- the sender ----------------------------------------------------------------


async def test_the_answer_closes_the_sealed_stream_with_what_is_left(
    _a_completed_run,
) -> None:
    run_id = uuid4()
    await record_live_display(
        run_id,
        conversation_id=uuid4(),
        handle=STREAM,
        streamed="Here is the ",
        sealed=True,
    )
    egress = _Egress()
    result = await _send(egress, run_id, "Here is the answer.")

    assert result.sent
    assert [call["message"] for call in egress.finished] == ["answer."]
    assert egress.finished[0]["progress_handle"].message_id == STREAM.message_id
    assert egress.finished[0]["already_streamed"] is True
    assert egress.posted == [] and egress.cleared == []
    # The answer is the stream's message: its id is the delivery's.
    assert result.external_message_id == STREAM.message_id
    # Noted before the close: a sender dying after it leaves it known sent.
    assert _a_completed_run == [STREAM.message_id]
    # Finished: a later look finds nothing left to close.
    assert await take_live_display(run_id, wait_seconds=0) is None


async def test_a_refused_close_takes_the_stream_down_and_posts_the_answer(
    _a_completed_run,
) -> None:
    run_id = uuid4()
    await record_live_display(
        run_id, conversation_id=uuid4(), handle=STREAM, streamed="Here", sealed=True
    )
    egress = _Egress(finish_result=False)
    result = await _send(egress, run_id, "Here is the answer.")

    assert result.sent
    assert len(egress.cleared) == 1
    assert [call["message"] for call in egress.posted] == ["Here is the answer."]
    # The id is the new message's, not the stream's that was taken down.
    assert result.external_message_id == "1700000009.1"
    assert await take_live_display(run_id, wait_seconds=0) is None
    # The stream's id, noted before the close, is forgotten once the stream
    # is gone -- a sender dying now leaves no sign the answer arrived -- and
    # the new message's is noted as the platform answers.
    assert _a_completed_run[-2:] == [None, "1700000009.1"]


async def test_an_unsealed_stream_that_disagrees_is_not_trusted(monkeypatch) -> None:
    monkeypatch.setattr(live_display, "SEAL_WAIT_SECONDS", 0.3)
    run_id = uuid4()
    await record_live_display(
        run_id, conversation_id=uuid4(), handle=STREAM, streamed="Draft that was"
    )
    egress = _Egress()
    result = await _send(egress, run_id, "Here is the answer.")

    assert result.sent
    assert egress.finished == []
    assert len(egress.cleared) == 1
    assert [call["message"] for call in egress.posted] == ["Here is the answer."]


async def test_without_a_stream_the_answer_is_posted(monkeypatch) -> None:
    monkeypatch.setattr(live_display, "SEAL_WAIT_SECONDS", 0.0)
    egress = _Egress()
    result = await _send(egress, uuid4(), "Here is the answer.")
    assert result.sent
    assert egress.finished == [] and egress.cleared == []
    assert [call["message"] for call in egress.posted] == ["Here is the answer."]


# --- the observer --------------------------------------------------------------


def _observer(egress: _Egress) -> SurfaceAgentRunProgressObserver:
    return SurfaceAgentRunProgressObserver(
        contact_gate=_no_contact_refuses,
        uow_factory=_Uow(),
        egress_factory=lambda _uow: egress,
    )


def _material(status: str, answer: str | None):
    async def read(uow, run_id):
        del uow, run_id
        return channel_replies.RunReplyMaterial(
            status=status, answer=answer, error=None, private=False
        )

    return read


async def _streamed_run(egress: _Egress, run_id) -> tuple:
    observer = _observer(egress)
    conversation = SimpleNamespace(
        id=uuid4(), contact_id=None, metadata={"surface_platform": "SLACK"}
    )
    ctx = _run_ctx(conversation, agent_run_id=run_id)
    await observer.on_run_started(conversation, ctx)
    observer._token_buffer = "Here is the answer."  # noqa: SLF001 - unflushed tail
    return observer, conversation, ctx


async def test_a_completed_run_leaves_its_stream_sealed_for_the_reply(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        channel_replies, "run_reply_material", _material("COMPLETED", "Here is it.")
    )
    egress, run_id = _Egress(), uuid4()
    observer, conversation, ctx = await _streamed_run(egress, run_id)

    await observer.on_run_finished(conversation, ctx)

    assert egress.cleared == [], "the stream must stay up for the reply to close"
    assert egress.appended[-1] == "Here is the answer.", "flushed to the last token"
    display = await take_live_display(run_id, wait_seconds=0)
    assert display is not None and display.sealed
    assert display.streamed.endswith("Here is the answer.")


async def test_a_run_with_no_answer_to_close_it_takes_the_stream_down(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        channel_replies, "run_reply_material", _material("STOPPED", None)
    )
    egress, run_id = _Egress(), uuid4()
    observer, conversation, ctx = await _streamed_run(egress, run_id)

    await observer.on_run_finished(conversation, ctx)

    assert len(egress.cleared) == 1
    assert await take_live_display(run_id, wait_seconds=0) is None


async def _no_contact_refuses(_conversation_id) -> bool:
    """No contact here refuses the answer (``contact_stream_gate``)."""
    return False
