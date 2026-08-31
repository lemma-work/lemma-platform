from __future__ import annotations

import io
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
import structlog

from app.core.log.log import setup_logging
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.value_objects import MessageKind
from app.modules.agent.infrastructure.repositories.conversation_opening_texts import (
    ConversationOpeningTexts,
)
from app.modules.agent.services import conversation_title_service as cts
from app.modules.agent.services.conversation_title_service import (
    ConversationTitleService,
    _sanitize_title,
    title_matches_user_script,
)
from app.modules.agent.services.realtime import title_updated_payload
from app.modules.usage.domain.entities import UsageReservation


# --- fakes ---------------------------------------------------------------


class _FakeCounter:
    """Records what the OTel counter was told, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, dict]] = []

    def add(self, amount: int, attributes: dict | None = None) -> None:
        self.calls.append((amount, attributes or {}))

    @property
    def outcomes(self) -> list[str]:
        return [attributes.get("outcome") for _, attributes in self.calls]


def _one(records: list[dict], event: str) -> dict:
    """The single record for ``event``; fails loudly when it is missing."""
    matching = [record for record in records if record.get("event") == event]
    assert matching, f"{event} was not logged; saw {[r.get('event') for r in records]}"
    assert len(matching) == 1, f"{event} logged {len(matching)} times"
    return matching[0]


@pytest.fixture
def records_at_info():
    """Structured records as production emits them — INFO, not DEBUG.

    The level is the point of the fixture: these assertions must fail if a
    failure path is ever demoted back below INFO, which is exactly how the
    original bug stayed invisible.
    """
    setup_logging(
        "development", service_name="lemma-test", json_logs=True, log_level="INFO"
    )
    handler = next(
        candidate
        for candidate in logging.getLogger().handlers
        if isinstance(candidate.formatter, structlog.stdlib.ProcessorFormatter)
    )
    buffer = io.StringIO()
    original_stream = handler.stream
    handler.stream = buffer
    try:
        yield lambda: [
            json.loads(line) for line in buffer.getvalue().splitlines() if line
        ]
    finally:
        handler.stream = original_stream


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
        if include_messages:
            # Titling needs two rows. Loading the transcript to find them cost
            # seconds of materialisation inside an open transaction.
            raise AssertionError(
                "titling must not load the transcript; "
                "use get_conversation_opening_texts"
            )
        return self.uow.conversation

    async def get_conversation_opening_texts(self, conversation_id):
        """The real query's answer, computed the way the old scan computed it."""
        conversation = self.uow.conversation
        messages = conversation.ordered_messages() if conversation else []

        def _first(role: str) -> str | None:
            for message in messages:
                if (
                    message.role == role
                    and message.kind == MessageKind.TEXT
                    and message.text
                    and message.text.strip()
                ):
                    return message.text.strip()
            return None

        return ConversationOpeningTexts(
            user_text=_first("user"), assistant_text=_first("assistant")
        )

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

        async def run(self, prompt, *, usage_limits=None, model_settings=None):
            capture["run_calls"] = int(capture["run_calls"]) + 1
            capture["prompt"] = prompt
            capture["usage_limits"] = usage_limits
            capture["model_settings"] = model_settings
            if raise_on_run:
                raise RuntimeError("llm boom")
            return SimpleNamespace(output=output)

    async def _reserve(*, organization_id, user_id, runtime_profile):
        del runtime_profile
        return UsageReservation(
            organization_id=organization_id,
            user_id=user_id,
            amount_usd=0.01,
        )

    async def _record(*, ctx, runtime_profile, result, status, reservation, metadata):
        capture["usage"].append(status)  # type: ignore[union-attr]

    async def _publish(conversation_id, payload):
        capture["published"].append((conversation_id, payload))  # type: ignore[union-attr]

    # LLM titling is opt-in via CONVERSATION_TITLE_MODEL; enable it for the
    # LLM-path tests regardless of the ambient .env value.
    monkeypatch.setattr(
        cts.agent_settings,
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


def _conversation(
    *,
    title=None,
    with_user=True,
    with_reply=True,
    user_text="Help me plan a 5-day trip to Japan in spring.",
) -> Conversation:
    conv = Conversation(user_id=uuid4(), pod_id=uuid4(), title=title)
    seq = 0
    if with_user:
        conv.messages.append(
            Message(
                conversation_id=conv.id,
                sequence=seq,
                role="user",
                kind=MessageKind.TEXT,
                text=user_text,
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
    # A reasoning model must not spend the token budget on a hidden
    # chain-of-thought trace for a 3-6 word title (see conversation_title_service
    # module docstring / _TITLE_MODEL_SETTINGS).
    assert capture["model_settings"] == {"openai_reasoning_effort": "none"}


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
    assert capture["published"] == [(conv.id, title_updated_payload(conv.id, title))]


@pytest.mark.asyncio
async def test_no_model_configured_uses_first_message_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _patch_llm(monkeypatch, output="Should Not Be Used")
    # Opt OUT of LLM titling: no model configured.
    monkeypatch.setattr(cts.agent_settings, "conversation_title_model", None)
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
    assert capture["published"] == [(conv.id, title_updated_payload(conv.id, title))]


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


async def test_llm_failure_is_visible_at_production_log_level(
    monkeypatch: pytest.MonkeyPatch, records_at_info
) -> None:
    """The record this whole change exists for.

    The handler used to be ``logger.debug`` with no ``exc_info``. Production
    runs at INFO behind a filtering bound logger, so the record was dropped
    before formatting: a provider outage and a healthy system looked identical,
    and the job counter said ``succeeded`` either way.
    """
    _patch_llm(monkeypatch, raise_on_run=True)
    conv = _conversation()
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    # The fallback title: the user's own first message, which is precisely what
    # "the title didn't generate" looks like from the outside.
    assert title == "Help me plan a 5-day trip to Japan in spring."
    failure = _one(records_at_info(), "agent.conversation_title.llm_call.failed")
    assert failure["level"] == "error"
    assert failure["error_type"] == "RuntimeError"
    assert "llm boom" in failure["error_message"]
    assert "\n" in failure["error_traceback"], "a one-line traceback is useless"


async def test_unexpected_failure_is_logged_and_counted_without_raising(
    monkeypatch: pytest.MonkeyPatch, records_at_info
) -> None:
    """A broken read must not raise, but must stop reporting success."""
    _patch_llm(monkeypatch)
    conv = _conversation()
    uow = _FakeUow(conv)

    class _BrokenRepo(_FakeRepo):
        async def get_conversation(self, conversation_id, **kwargs):
            raise RuntimeError("database down")

    monkeypatch.setattr(cts, "ConversationRepository", _BrokenRepo)
    counter = _FakeCounter()
    monkeypatch.setattr(cts, "title_counter", counter)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    assert title is None, "titling must never break the calling worker"
    failure = _one(records_at_info(), "agent.conversation_title.generation.failed")
    assert failure["level"] == "error"
    assert failure["error_type"] == "RuntimeError"
    assert "database down" in failure["error_message"]
    assert counter.outcomes == ["failed"]


@pytest.mark.parametrize(
    ("raise_on_run", "expected"),
    [(False, "llm"), (True, "fallback")],
)
async def test_counter_says_which_title_path_ran(
    monkeypatch: pytest.MonkeyPatch, raise_on_run: bool, expected: str
) -> None:
    """ "Half the time it doesn't work" is answerable only if the two differ."""
    _patch_llm(monkeypatch, output="Japan Trip", raise_on_run=raise_on_run)
    conv = _conversation()
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)
    counter = _FakeCounter()
    monkeypatch.setattr(cts, "title_counter", counter)

    await ConversationTitleService(uow_factory=uow).generate_title_if_absent(conv.id)

    assert counter.outcomes == [expected]


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


# --- the title is in the language the person wrote in ---------------------
#
# Reported from production: a conversation opened with a greeting came back
# titled 初识寒暄. The system prompt already asked for the user's language; what
# it did not say was what to do when the opening message has no language to
# match, which is exactly the case that broke. The prompt now names English as
# the default, and these pin the guard that holds when the prompt does not.


def test_a_latin_title_always_passes() -> None:
    assert title_matches_user_script("Five Day Japan Itinerary", "hi") is True
    # Digits and punctuation carry no language.
    assert title_matches_user_script("Q4 2026 Revenue (draft)", "hi") is True


def test_a_title_in_a_script_the_person_never_used_is_rejected() -> None:
    assert title_matches_user_script("初识寒暄", "hi") is False
    assert title_matches_user_script("Привет мир", "hello there") is False
    assert title_matches_user_script("데이터 정리", "clean up my data") is False


def test_a_title_matching_the_person_is_kept() -> None:
    assert title_matches_user_script("初识寒暄", "你好，帮我看一下") is True
    assert title_matches_user_script("Планы на квартал", "Привет, помоги мне") is True
    # A mixed-script message licenses either script.
    assert title_matches_user_script("items 表行数", "查询 items 表的行数") is True


@pytest.mark.asyncio
async def test_a_mismatched_title_falls_back_to_the_persons_own_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(monkeypatch, output="初识寒暄")
    conv = _conversation(user_text="hi", with_reply=False)
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)
    counter = _FakeCounter()
    monkeypatch.setattr(cts, "title_counter", counter)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    assert title == "hi"
    assert uow.updated_with is not None and uow.updated_with.title == "hi"
    # Counted as the fallback path, because that is the one that produced it.
    assert counter.outcomes == ["fallback"]


@pytest.mark.asyncio
async def test_a_chinese_conversation_still_gets_a_chinese_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The guard must not become "titles are English now".
    _patch_llm(monkeypatch, output="查询items表行数")
    conv = _conversation(user_text="查询 items 表有多少行", with_reply=False)
    uow = _FakeUow(conv)
    monkeypatch.setattr(cts, "ConversationRepository", _FakeRepo)

    title = await ConversationTitleService(uow_factory=uow).generate_title_if_absent(
        conv.id
    )

    assert title == "查询items表行数"


def test_the_prompt_names_english_as_the_default() -> None:
    # The mismatch guard is the backstop; the prompt is what should stop this
    # reaching it. A rule that only says "match the user" leaves the
    # no-language-to-match case undefined, which is the case that failed.
    prompt = cts._TITLE_SYSTEM_PROMPT
    assert "same script" in prompt
    assert "English is the default" in prompt
    assert "Never translate" in prompt
