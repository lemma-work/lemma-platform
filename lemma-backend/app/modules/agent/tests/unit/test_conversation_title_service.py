from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.value_objects import MessageKind
from app.modules.agent.services import conversation_title_service as cts
from app.modules.agent.services.conversation_title_service import (
    ConversationTitleService,
    _sanitize_title,
)
from app.modules.agent.services.realtime import title_updated_payload


# --- fakes ---------------------------------------------------------------


class _FakeUow:
    """Doubles as the uow_factory and the uow it yields."""

    def __init__(self, conversation: Conversation | None) -> None:
        self.conversation = conversation
        self.committed = False
        self.updated_with: Conversation | None = None

    def __call__(self) -> "_FakeUow":
        return self

    async def __aenter__(self) -> "_FakeUow":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


class _FakeRepo:
    def __init__(self, uow: _FakeUow) -> None:
        self.uow = uow

    async def get_conversation(
        self, conversation_id, *, include_messages=False, include_runs=False
    ) -> Conversation | None:
        return self.uow.conversation

    async def update_conversation(self, conversation: Conversation) -> Conversation:
        self.uow.updated_with = conversation
        return conversation


class _FakeResolved:
    credentials: dict[str, object] = {}
    model_name_for_harness = "deepseek-v4-flash"

    def public_snapshot(self) -> dict[str, object | None]:
        return {
            "profile_id": "system:lemma",
            "scope": "SYSTEM",
            "protocol": "OPENAI_COMPATIBLE",
            "model_name": "deepseek-v4-flash",
            "provider_model_name": "accounts/fireworks/models/deepseek-v4-flash",
            "config": {"base_url": "http://fireworks.test/v1"},
        }


class _FakeProfileService:
    async def resolve(self, *, runtime, organization_id, user_id):
        return _FakeResolved()


def _patch_llm(
    monkeypatch: pytest.MonkeyPatch,
    *,
    output: str = "A nice title",
    raise_on_run: bool = False,
) -> dict[str, object]:
    """Stub the model/LLM/usage boundary. Returns a capture dict."""
    capture: dict[str, object] = {"run_calls": 0, "usage": [], "published": []}

    class _FakeLLMAgent:
        def __init__(self, model, system_prompt=None):  # noqa: D401
            capture["model"] = model
            capture["system_prompt"] = system_prompt

        async def run(self, prompt):
            capture["run_calls"] = int(capture["run_calls"]) + 1
            capture["prompt"] = prompt
            if raise_on_run:
                raise RuntimeError("llm boom")
            return SimpleNamespace(output=output)

    async def _reserve(*, organization_id, user_id, runtime_profile):
        return None

    async def _record(*, ctx, runtime_profile, result, status, reservation, metadata):
        capture["usage"].append(status)  # type: ignore[union-attr]

    async def _publish(conversation_id, payload):
        capture["published"].append((conversation_id, payload))  # type: ignore[union-attr]

    # LLM titling is opt-in via CONVERSATION_TITLE_MODEL; enable it for the
    # LLM-path tests regardless of the ambient .env value.
    monkeypatch.setattr(
        cts.settings,
        "conversation_title_model",
        "accounts/fireworks/models/test-title-model",
    )
    monkeypatch.setattr(cts, "PydanticAIAgent", _FakeLLMAgent)
    monkeypatch.setattr(cts, "AgentRuntimeProfileService", _FakeProfileService)
    monkeypatch.setattr(
        cts,
        "require_pydantic_ai_model_from_runtime_profile",
        lambda **_: object(),
    )
    monkeypatch.setattr(cts, "reserve_usage_for_runtime", _reserve)
    monkeypatch.setattr(cts, "record_pydantic_ai_result_usage", _record)
    monkeypatch.setattr(cts, "publish_conversation_event", _publish)
    return capture


def _conversation(*, title=None, with_user=True, with_reply=True) -> Conversation:
    conv = Conversation(user_id=uuid4(), pod_id=uuid4(), title=title)
    seq = 0
    if with_user:
        conv.messages.append(
            Message(
                conversation_id=conv.id,
                sequence=seq,
                role="user",
                kind=MessageKind.TEXT,
                text="Help me plan a 5-day trip to Japan in spring.",
            )
        )
        seq += 1
    if with_reply:
        conv.messages.append(
            Message(
                conversation_id=conv.id,
                sequence=seq,
                role="assistant",
                kind=MessageKind.TEXT,
                text="Sure! Here is a suggested itinerary...",
            )
        )
    return conv


# --- tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_generates_persists_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _patch_llm(monkeypatch, output='  "Japan Spring Trip Plan".  ')
    conv = _conversation()
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    assert title == "Japan Spring Trip Plan"  # quotes + trailing period stripped
    assert uow.updated_with is not None
    assert uow.updated_with.title == "Japan Spring Trip Plan"
    assert uow.committed is True
    assert capture["usage"] == ["COMPLETED"]
    assert capture["published"] == [
        (conv.id, title_updated_payload(conv.id, "Japan Spring Trip Plan"))
    ]
    # Reply text is fed into the prompt alongside the user message.
    assert "Assistant's reply" in str(capture["prompt"])


@pytest.mark.asyncio
async def test_idempotent_when_title_already_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _patch_llm(monkeypatch)
    conv = _conversation(title="Existing title")
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    assert title is None
    assert capture["run_calls"] == 0  # LLM never invoked
    assert uow.updated_with is None
    assert capture["published"] == []


@pytest.mark.asyncio
async def test_skips_when_no_user_message(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _patch_llm(monkeypatch)
    conv = _conversation(with_user=False, with_reply=True)
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    assert title is None
    assert capture["run_calls"] == 0
    assert uow.updated_with is None


@pytest.mark.asyncio
async def test_llm_error_falls_back_to_first_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _patch_llm(monkeypatch, raise_on_run=True)
    conv = _conversation()
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    # LLM failed but titling still succeeds via the first-message fallback.
    assert title == "Help me plan a 5-day trip to Japan in spring."
    assert capture["usage"] == ["FAILED"]  # failure still metered
    assert uow.updated_with is not None
    assert uow.updated_with.title == title
    assert capture["published"] == [
        (conv.id, title_updated_payload(conv.id, title))
    ]


@pytest.mark.asyncio
async def test_no_model_configured_uses_first_message_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _patch_llm(monkeypatch, output="Should Not Be Used")
    # Opt OUT of LLM titling: no model configured.
    monkeypatch.setattr(cts.settings, "conversation_title_model", None)
    conv = _conversation()
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    assert title == "Help me plan a 5-day trip to Japan in spring."
    assert capture["run_calls"] == 0  # LLM never invoked
    assert uow.updated_with is not None
    assert uow.updated_with.title == title
    assert capture["published"] == [
        (conv.id, title_updated_payload(conv.id, title))
    ]


@pytest.mark.asyncio
async def test_works_without_assistant_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = _patch_llm(monkeypatch, output="Japan Trip")
    conv = _conversation(with_reply=False)
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    assert title == "Japan Trip"
    assert "Assistant's reply" not in str(capture["prompt"])


def test_title_updated_payload_shape() -> None:
    conversation_id = uuid4()
    payload = title_updated_payload(conversation_id, "My Title")
    assert payload == {
        "type": "title",
        "data": {"conversation_id": str(conversation_id), "title": "My Title"},
    }


def test_sanitize_title_strips_and_clamps() -> None:
    assert _sanitize_title('"Hello World".') == "Hello World"
    assert _sanitize_title("First line\nsecond line") == "First line"
    assert _sanitize_title("  spaced  ") == "spaced"
    long = "word " * 40
    assert len(_sanitize_title(long)) <= 80
    assert _sanitize_title("") == ""
