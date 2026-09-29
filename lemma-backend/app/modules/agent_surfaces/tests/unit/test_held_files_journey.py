"""Files a run showed, from the tool that held them to the email that carries them.

These follow the whole journey with the real store, egress and observer over an
in-memory Redis and a fake mail transport. A helper-level test of the store
cannot notice that the observer's end-of-run cleanup throws away what a failed
send was supposed to keep, or that a later turn's reply picks up an earlier
run's files: those are properties of the components together.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import fakeredis
import pytest

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
)
from app.modules.agent_surfaces.domain.entities import AgentSurfaceConversationLink
from app.modules.agent_surfaces.domain.envelope import EnvelopeFile
from app.modules.agent_surfaces.services import one_reply_attachments, pending_envelope
from app.modules.agent_surfaces.services.display_resource_content import (
    PodFileDelivery,
    PodFileParts,
)
from app.modules.agent_surfaces.services.pending_envelope import (
    RunFiles,
    held_display_paths,
    remember_display_path,
)
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _delivering_adapter,
    _resend_surface,
    _slack_event,
    build_egress,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


class _YieldingRedis:
    """Fakeredis that lets every other task run between any two commands.

    A read-modify-write is only ever safe if nothing can run between its read
    and its write; making every command a scheduling point is what shows whether
    one is being relied on.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        async def call(*args, **kwargs):
            await asyncio.sleep(0)
            result = attribute(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            await asyncio.sleep(0)
            return result

        return call

    def pipeline(self, *args, **kwargs):
        return self._inner.pipeline(*args, **kwargs)


@pytest.fixture(autouse=True)
def held_store():
    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="test:held-journey", ttl_seconds=60
    )
    cache._redis = _YieldingRedis(fakeredis.FakeAsyncRedis(decode_responses=True))
    previous = pending_envelope._cache
    pending_envelope._cache = cache
    yield cache
    pending_envelope._cache = previous


# --- concurrent writers ------------------------------------------------------


async def test_every_file_shown_concurrently_is_held_exactly_once():
    conversation = uuid4()
    run = RunFiles(uuid4())
    paths = [f"/me/{i}.pdf" for i in range(15)]

    results = await asyncio.gather(
        *(remember_display_path(conversation, run, path) for path in paths)
    )

    assert all(results)
    held = await held_display_paths(conversation, run)
    assert sorted(held) == sorted(paths)
    assert len(held) == len(set(held))


async def test_the_same_file_shown_concurrently_is_still_one_attachment():
    conversation = uuid4()
    run = RunFiles(uuid4())

    results = await asyncio.gather(
        *(remember_display_path(conversation, run, "/me/q3.pdf") for _ in range(6))
    )

    assert all(results)
    assert await held_display_paths(conversation, run) == ["/me/q3.pdf"]


async def test_racing_for_the_last_slots_never_holds_more_than_the_limit():
    conversation = uuid4()
    run = RunFiles(uuid4())

    results = await asyncio.gather(
        *(remember_display_path(conversation, run, f"/me/{i}.pdf") for i in range(30))
    )

    assert results.count(True) == 20
    held = await held_display_paths(conversation, run)
    assert len(held) == 20
    # What was told "taken" is exactly what is held: no file the model was told
    # was queued went missing, and none that was refused sneaked in.
    accepted = {f"/me/{i}.pdf" for i, ok in enumerate(results) if ok}
    assert set(held) == accepted


async def test_files_come_out_in_the_order_they_were_shown():
    conversation = uuid4()
    run = RunFiles(uuid4())

    for name in ("c", "a", "b"):
        await remember_display_path(conversation, run, f"/me/{name}.pdf")

    assert await held_display_paths(conversation, run) == [
        "/me/c.pdf",
        "/me/a.pdf",
        "/me/b.pdf",
    ]


# --- a failed final email keeps its files, for that run only -----------------


@pytest.fixture
def pod_files(monkeypatch):
    """The datastore boundary: a held path resolves to an attachment named for it."""

    async def resolve(*, path: str, **_: object) -> PodFileParts:
        return PodFileParts(
            files=[
                EnvelopeFile(
                    file_name=path.rsplit("/", 1)[-1],
                    content=path.encode(),
                    mime_type="application/pdf",
                )
            ],
            facts=PodFileDelivery(delivered=True),
        )

    monkeypatch.setattr(one_reply_attachments, "resolve_pod_file_parts", resolve)


class _UowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _email_world(conversation_id: UUID):
    surface = _resend_surface()
    parsed = _slack_event()
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="RESEND",
        external_channel_id=parsed.external_channel_id,
        external_thread_id=parsed.external_thread_id,
        external_user_id=parsed.sender_external_user_id,
        last_event=parsed.model_dump(mode="json"),
    )
    adapter = _delivering_adapter("RESEND")
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    return egress, adapter


def _observer_for(egress) -> SurfaceAgentRunProgressObserver:
    return SurfaceAgentRunProgressObserver(
        uow_factory=_UowFactory(), egress_factory=lambda _uow: egress
    )


async def _run_to_the_end(observer, conversation, run_id: UUID, answer: str) -> None:
    ctx = SimpleNamespace(agent_run_id=run_id)
    await observer.on_run_started(conversation, ctx)
    await observer.on_event(
        AgentEvent(type=AgentEventType.MESSAGE, data=MessageDraft.of_text(answer)),
        conversation,
        ctx,
    )
    await observer.on_run_finished(conversation, ctx)


def _attachments(adapter, call_index: int) -> list[str]:
    metadata = adapter.send_message.await_args_list[call_index].kwargs["metadata"]
    return [name for name, _content, _mime in metadata.get("attachments", [])]


async def test_a_failed_final_email_keeps_its_files_for_that_run_and_no_other(
    pod_files,
):
    conversation_id = uuid4()
    conversation = SimpleNamespace(
        id=conversation_id, metadata={"surface_platform": "RESEND"}
    )
    egress, adapter = _email_world(conversation_id)
    run_a, run_b = uuid4(), uuid4()
    await remember_display_path(conversation_id, RunFiles(run_a), "/me/q3.pdf")

    # Run A's final email fails to go out.
    adapter.send_message.side_effect = TimeoutError("resend is down")
    await _run_to_the_end(
        _observer_for(egress), conversation, run_a, "Here is the Q3 report."
    )
    assert await held_display_paths(conversation_id, RunFiles(run_a)) == [
        "/me/q3.pdf"
    ], "the failed reply's files survive the end of the run"

    # A later turn's reply, and a failure notice, must not carry them.
    adapter.send_message.side_effect = None
    adapter.send_message.reset_mock()
    await egress.send_agent_message_for_conversation(
        conversation_id=conversation_id,
        message="I could not finish that.",
        metadata={"retry_action": True},
        attach_files_of=RunFiles(run_a),
    )
    await _run_to_the_end(
        _observer_for(egress), conversation, run_b, "An unrelated answer."
    )
    assert [_attachments(adapter, i) for i in range(2)] == [[], []]
    assert await held_display_paths(conversation_id, RunFiles(run_a)) == ["/me/q3.pdf"]

    # Recovering run A's own reply attaches the files, and only then releases them.
    adapter.send_message.reset_mock()
    delivered = await egress.send_agent_message_for_conversation(
        conversation_id=conversation_id,
        message="Here is the Q3 report.",
        attach_files_of=RunFiles(run_a),
    )
    assert delivered is True
    assert _attachments(adapter, 0) == ["q3.pdf"]
    assert await held_display_paths(conversation_id, RunFiles(run_a)) == []


async def test_a_delivered_final_email_releases_its_files_with_the_run(pod_files):
    conversation_id = uuid4()
    conversation = SimpleNamespace(
        id=conversation_id, metadata={"surface_platform": "RESEND"}
    )
    egress, adapter = _email_world(conversation_id)
    run = uuid4()
    await remember_display_path(conversation_id, RunFiles(run), "/me/q3.pdf")

    await _run_to_the_end(
        _observer_for(egress), conversation, run, "Here is the Q3 report."
    )

    assert _attachments(adapter, 0) == ["q3.pdf"]
    assert await held_display_paths(conversation_id, RunFiles(run)) == []


async def test_a_run_that_ends_without_a_reply_discards_what_it_held(pod_files):
    conversation_id = uuid4()
    conversation = SimpleNamespace(
        id=conversation_id, metadata={"surface_platform": "RESEND"}
    )
    egress, _adapter = _email_world(conversation_id)
    run = uuid4()
    await remember_display_path(conversation_id, RunFiles(run), "/me/q3.pdf")
    observer = _observer_for(egress)
    ctx = SimpleNamespace(agent_run_id=run)

    # No answer text at all: nothing will ever carry the files.
    await observer.on_run_started(conversation, ctx)
    await observer.on_run_finished(conversation, ctx)

    assert await held_display_paths(conversation_id, RunFiles(run)) == []


async def test_a_notification_never_carries_a_runs_held_files(pod_files):
    conversation_id = uuid4()
    egress, adapter = _email_world(conversation_id)
    await remember_display_path(conversation_id, RunFiles(uuid4()), "/me/q3.pdf")

    await egress.send_agent_message_for_conversation(
        conversation_id=conversation_id, message="A notification."
    )

    assert _attachments(adapter, 0) == []
