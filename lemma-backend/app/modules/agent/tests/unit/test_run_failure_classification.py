"""One failure, one explanation, whichever layer caught it.

The harness and the runner each turned an exception into a sentence, written
apart and drifting: the same exception read differently depending on where it
surfaced, and a response cut off by the output cap was reported as a tool
failing repeatedly, sending people to check an agent configuration that was
fine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import httpx2
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingCalls,
    DeltaThinkingPart,
    FunctionModel,
)

from app.core.domain.errors import DomainError
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import AgentEventType
from app.modules.agent.infrastructure.harness.lemma.provider_error_log import (
    failure_event,
    user_facing_error_message,
)
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness
from app.modules.agent.infrastructure.harness.lemma.run_failure_classification import (
    PROVIDER_HTTP_ERROR,
    TRANSIENT_DATABASE_ERROR,
    classify_run_failure,
    run_failure_reason,
)
from app.modules.agent.infrastructure.harness.lemma.retry import (
    HarnessDriverCancelled,
)
from app.modules.agent.infrastructure.transport_errors import (
    ModelResponseBudgetExceeded,
)
from app.modules.agent.services.run_finalizer import (
    run_failure_code,
    run_failure_message,
)
from app.modules.usage.contracts import UsageLimitExceededError

pytestmark = pytest.mark.unit

_REQUEST = httpx2.Request("POST", "https://provider.example/v1/chat")
_OUTPUT_ROOM = "ran out of room to answer"


def _http(status: int, body: object = None) -> ModelHTTPError:
    return ModelHTTPError(status, "private-model", body=body)


class _DriverError(Exception):
    """The asyncpg error inside a ``DBAPIError``, reduced to its SQLSTATE."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate


def _database(sqlstate: str) -> DBAPIError:
    return DBAPIError("SELECT ... FOR UPDATE", {}, _DriverError(sqlstate))


def _wrapped(cause: BaseException) -> RuntimeError:
    try:
        raise RuntimeError("dispatch failed") from cause
    except RuntimeError as wrapped:
        return wrapped


_FAILURES: list[tuple[str, Exception]] = [
    ("rate limited", _http(429)),
    ("billing", _http(402)),
    ("provider down", _http(503)),
    ("key rejected", _http(401)),
    ("no such model", _http(404)),
    (
        "context window",
        _http(400, {"error": {"code": "context_length_exceeded"}}),
    ),
    ("other 4xx", _http(418)),
    ("dropped connection", httpx2.ReadError("connection reset")),
    ("dropped connection, httpx stack", httpx.ReadError("connection reset")),
    (
        "over the time budget",
        ModelResponseBudgetExceeded(
            "x", request=_REQUEST, elapsed_seconds=901.0, budget_seconds=900.0
        ),
    ),
    ("cut off mid tool call", IncompleteToolCall("Model token limit (10) exceeded")),
    (
        "thought until the cap",
        UnexpectedModelBehavior(
            "Model token limit (10) exceeded before any response was generated."
        ),
    ),
    ("invalid tool arguments", UnexpectedModelBehavior("Tool exceeded max retries")),
    ("pydantic-ai usage limit", UsageLimitExceeded("request_limit of 5")),
    ("driver cancelled", HarnessDriverCancelled("cancelled")),
    ("workspace allowance", UsageLimitExceededError()),
    ("domain error", DomainError("A sentence written for the reader.", code="X")),
    ("deadlock", _database("40P01")),
    ("anything else", ValueError("private detail")),
]


@pytest.mark.parametrize(
    "exc", [exc for _, exc in _FAILURES], ids=[name for name, _ in _FAILURES]
)
def test_the_harness_and_the_runner_say_the_same_thing(exc: Exception) -> None:
    assert user_facing_error_message(exc) == run_failure_message(exc)


@pytest.mark.parametrize(
    "exc",
    [
        IncompleteToolCall("Model token limit (10) exceeded while generating"),
        UnexpectedModelBehavior(
            "Model token limit (10) exceeded before any response was generated."
        ),
    ],
)
def test_running_out_of_output_room_is_not_blamed_on_a_tool(exc: Exception) -> None:
    message = run_failure_message(exc)

    assert _OUTPUT_ROOM in message
    assert "tool" not in message.lower()


def test_a_tool_that_kept_failing_is_still_named_as_one() -> None:
    message = run_failure_message(UnexpectedModelBehavior("Tool exceeded max retries"))

    assert "A tool failed repeatedly" in message


async def test_a_model_that_only_thought_until_the_cap_says_it_ran_out_of_room() -> (
    None
):
    """A reasoning model that spends its whole output budget thinking."""

    async def think(messages, info: AgentInfo) -> AsyncIterator[DeltaThinkingCalls]:
        del messages, info
        yield {0: DeltaThinkingPart(content="Let me consider every angle...")}

    class _CutOffAtTheCap(FunctionModel):
        """Streams only thinking, then stops the way a provider does at the cap."""

        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            async with super().request_stream(*args, **kwargs) as response:
                response.finish_reason = "length"
                yield response

    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    events = [
        event
        async for event in PydanticAIHarness(
            model=_CutOffAtTheCap(stream_function=think)
        ).run(
            agent=Agent(
                pod_id=pod_id,
                user_id=conversation.user_id,
                name="assistant",
                instruction="",
            ),
            conversation=conversation,
            messages=[],
            ctx=ToolContext(
                user_id=conversation.user_id,
                pod_id=pod_id,
                conversation_id=conversation.id,
            ),
            options=HarnessOptions(
                model_name="test-model", history_summarization_enabled=False
            ),
            agent_run_id=uuid4(),
        )
    ]

    (error,) = [e for e in events if e.type is AgentEventType.ERROR]
    assert _OUTPUT_ROOM in str(error.data)
    assert "tool" not in str(error.data).lower()


_DATABASE_BUSY = [
    pytest.param(_database("40P01"), id="deadlock"),
    pytest.param(_database("40001"), id="serialization failure"),
    pytest.param(PoolTimeoutError("QueuePool limit reached"), id="pool exhausted"),
    pytest.param(_wrapped(_database("40P01")), id="deadlock as a cause"),
]


@pytest.mark.parametrize("exc", _DATABASE_BUSY)
def test_a_busy_database_is_not_blamed_on_the_model_settings(
    exc: BaseException,
) -> None:
    """Twenty runs started at once; the ones Postgres aborted read "check the model".

    Nothing about the teammate was wrong, so nothing should send the reader to
    Settings -> Models, which is also the phrase the web app keys its "set up a
    model" prompt on.
    """
    failure = classify_run_failure(exc)

    assert failure.code == TRANSIENT_DATABASE_ERROR == run_failure_code(exc)
    assert "Settings → Models" not in failure.message
    assert "model" not in failure.message.lower()
    assert "send your message again" in failure.message


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(_database("23505"), id="unique violation"),
        pytest.param(_database("42P01"), id="missing table"),
    ],
)
def test_a_real_database_error_is_not_called_transient(exc: BaseException) -> None:
    assert classify_run_failure(exc).code != TRANSIENT_DATABASE_ERROR


@pytest.mark.parametrize("status", [400, 401, 404, 405, 429, 503])
def test_a_provider_http_error_has_a_stable_code_and_its_status(status: int) -> None:
    """A misconfigured base URL answers 405; the run used to record no code."""
    exc = _http(status)

    failure = classify_run_failure(exc)

    assert failure.code == PROVIDER_HTTP_ERROR == run_failure_code(exc)
    assert run_failure_reason(exc) == f"http_{status}"
    assert f"HTTP {status}" in failure.message


def test_an_exhausted_provider_retry_keeps_its_own_code() -> None:
    exhausted = DomainError("gave up", code="MODEL_PROVIDER_ATTEMPTS_EXHAUSTED")
    exhausted.__cause__ = _http(503)

    assert classify_run_failure(exhausted).code == "MODEL_PROVIDER_ATTEMPTS_EXHAUSTED"


async def test_a_run_the_provider_refused_ends_on_an_error_carrying_the_code() -> None:
    """The harness catches the refusal itself, so the code has to travel on the
    event: the runner's own classification never sees this exception."""

    async def refuse(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        raise _http(405)
        yield ""  # pragma: no cover - makes this an async generator

    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    events = [
        event
        async for event in PydanticAIHarness(
            model=FunctionModel(stream_function=refuse)
        ).run(
            agent=Agent(
                pod_id=pod_id,
                user_id=conversation.user_id,
                name="assistant",
                instruction="",
            ),
            conversation=conversation,
            messages=[],
            ctx=ToolContext(
                user_id=conversation.user_id,
                pod_id=pod_id,
                conversation_id=conversation.id,
            ),
            options=HarnessOptions(
                model_name="test-model", history_summarization_enabled=False
            ),
            agent_run_id=uuid4(),
        )
    ]

    (error,) = [e for e in events if e.type is AgentEventType.ERROR]
    assert error.error_code == PROVIDER_HTTP_ERROR
    assert error.error_reason == "http_405"
    assert "HTTP 405" in str(error.data)


async def test_the_pump_records_the_code_an_error_event_carries(monkeypatch) -> None:
    from app.modules.agent.services import run_event_pump as pump_module
    from app.modules.agent.services.run_identity import RunIdentity

    calls: list[dict[str, object]] = []

    class _Finalizer:
        async def finish(self, **kwargs: object) -> None:
            calls.append(kwargs)

    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())
    pump = pump_module.RunEventPump(message_writer=None, finalizer=_Finalizer())

    await pump.handle(
        event=failure_event(_http(405), agent_run_id=run.agent_run_id),
        run=run,
        outcome=pump_module.RunOutcome(),
    )

    (call,) = calls
    assert call["error_code"] == PROVIDER_HTTP_ERROR
    assert call["error_reason"] == "http_405"
