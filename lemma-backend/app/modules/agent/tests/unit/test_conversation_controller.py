import json
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.datastructures import QueryParams

from app.modules.agent.api.controllers import conversation_controller
from app.modules.agent.api.controllers.conversation_controller import (
    _parse_metadata_filters,
    append_message,
    retry_failed_run,
    send_message,
    stream_conversation,
)
from app.modules.agent.domain.entities import AgentRun
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationAgentScope,
)
from app.modules.test_support.authz import allow_all_context
from app.modules.usage.domain.errors import UsageLimitExceededError


def test_parse_metadata_filters_uses_metadata_dot_prefix() -> None:
    workflow_run_id = uuid4()

    filters = _parse_metadata_filters(
        query_params=[
            ("metadata.foo", "bar"),
            ("metadata.bar", "baz"),
            ("metadata.source", "WORKFLOW_RUN"),
            ("metadata.workflow_run_id", str(workflow_run_id)),
            ("agent_name", "researcher"),
        ],
    )

    assert filters == {
        "foo": "bar",
        "bar": "baz",
        "source": "WORKFLOW_RUN",
        "workflow_run_id": str(workflow_run_id),
    }


def test_parse_metadata_filters_rejects_empty_metadata_key() -> None:
    with pytest.raises(HTTPException):
        _parse_metadata_filters(
            query_params=[("metadata.", "bar")],
        )


def test_parse_metadata_filters_returns_none_without_metadata_filters() -> None:
    filters = _parse_metadata_filters(
        query_params=[
            ("source", "WORKFLOW_RUN"),
            ("workflow_run_id", "old-id"),
            ("agent_name", "researcher"),
        ],
    )

    assert filters is None


class _ConversationService:
    def __init__(
        self,
        result: AgentRunStartResult | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self.result = result
        self.exc = exc
        self.called = False

    async def add_user_message_and_start_run(self, **kwargs):
        self.called = True
        if self.exc is not None:
            raise self.exc
        return self.result

    async def retry_failed_run(self, **kwargs):
        self.called = True
        if self.exc is not None:
            raise self.exc
        return self.result


class _StreamConversationService:
    """Shaped like the real service: reads go through `.queries`."""

    def __init__(self, agent_run: AgentRun | None) -> None:
        self.agent_run = agent_run
        self.conversation_repository = SimpleNamespace(
            get_agent_run=AsyncMock(return_value=agent_run)
        )
        self.queries = _StreamConversationQueries(agent_run)


class _StreamConversationQueries:
    def __init__(self, agent_run: AgentRun | None) -> None:
        self.agent_run = agent_run

    async def get_conversation(self, **kwargs):
        return SimpleNamespace(id=kwargs["conversation_id"])

    async def get_active_agent_run(self, **kwargs):
        return self.agent_run


class _ConversationListService:
    def __init__(self) -> None:
        self.queries = _ConversationListQueries()

    @property
    def kwargs(self):
        return self.queries.kwargs


class _ConversationListQueries:
    def __init__(self) -> None:
        self.kwargs = None

    async def list_conversations(self, **kwargs):
        self.kwargs = kwargs
        return [], None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "agent_name", "expected_scope", "expected_value"),
    [
        ("", None, ConversationAgentScope.ALL, None),
        (
            "agent_name=POD_DEFAULT",
            "POD_DEFAULT",
            ConversationAgentScope.POD_DEFAULT,
            None,
        ),
        (
            "agent_name=pod_default",
            "pod_default",
            ConversationAgentScope.POD_DEFAULT,
            None,
        ),
        (
            "agent_name=researcher",
            "researcher",
            ConversationAgentScope.NAMED,
            "researcher",
        ),
    ],
)
async def test_list_conversations_parses_agent_selection(
    query,
    agent_name,
    expected_scope,
    expected_value,
) -> None:
    service = _ConversationListService()

    response = await conversation_controller.list_conversations(
        pod_id=uuid4(),
        request=SimpleNamespace(query_params=QueryParams(query)),
        user=SimpleNamespace(id=uuid4()),
        service=service,
        agent_name=agent_name,
        run_status=None,
        conversation_type=None,
        parent_id=None,
        page_token=None,
        limit=20,
    )

    assert response.items == []
    selection = service.kwargs["agent_selection"]
    assert selection.scope is expected_scope
    assert selection.value == expected_value


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["", "   "])
async def test_list_conversations_rejects_empty_agent_name(agent_name) -> None:
    service = _ConversationListService()

    with pytest.raises(HTTPException) as exc_info:
        await conversation_controller.list_conversations(
            pod_id=uuid4(),
            request=SimpleNamespace(
                query_params=QueryParams(f"agent_name={agent_name}")
            ),
            user=SimpleNamespace(id=uuid4()),
            service=service,
            agent_name=agent_name,
            run_status=None,
            conversation_type=None,
            parent_id=None,
            page_token=None,
            limit=20,
        )

    assert exc_info.value.status_code == 422
    assert service.kwargs is None


class _ChannelService:
    def __init__(self, iterator):
        self.iterator = iterator
        self.exited = False

    @asynccontextmanager
    async def subscribe(self, channels):
        try:
            yield self.iterator
        finally:
            self.exited = True


async def _empty_iterator():
    if False:
        yield None


async def _failing_iterator():
    raise RuntimeError("redis pubsub disconnected")
    if False:
        yield None


@asynccontextmanager
async def _mock_uow_factory(uow_mock):
    yield uow_mock


def _make_uow_factory():
    uow_mock = AsyncMock()
    return partial(_mock_uow_factory, uow_mock), uow_mock


@pytest.mark.asyncio
async def test_send_message_starts_run_before_stream_body_is_consumed(
    monkeypatch,
) -> None:
    result = AgentRunStartResult(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        started_new_run=True,
    )
    service = _ConversationService(result)
    channel_service = _ChannelService(_empty_iterator())
    uow_factory, _ = _make_uow_factory()
    monkeypatch.setattr(
        conversation_controller, "_build_conversation_service", lambda uow: service
    )
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=allow_all_context()),
    )

    response = await send_message(
        pod_id=uuid4(),
        conversation_id=result.conversation_id,
        data=SimpleNamespace(content="say ok", metadata=None),
        user=SimpleNamespace(id=uuid4()),
        channel_service=channel_service,
        request=SimpleNamespace(),
        uow_factory=uow_factory,
    )

    assert response.media_type == "text/event-stream"
    assert service.called is True


@pytest.mark.asyncio
async def test_retry_failed_run_returns_typed_start_response(
    monkeypatch,
) -> None:
    result = AgentRunStartResult(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        started_new_run=True,
    )
    service = _ConversationService(result)
    uow_factory, _ = _make_uow_factory()
    monkeypatch.setattr(
        conversation_controller,
        "_build_conversation_retry_service",
        lambda uow: service,
    )
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=allow_all_context()),
    )

    response = await retry_failed_run(
        pod_id=uuid4(),
        conversation_id=result.conversation_id,
        user=SimpleNamespace(id=uuid4()),
        request=SimpleNamespace(),
        uow_factory=uow_factory,
    )

    assert response.conversation_id == result.conversation_id
    assert response.agent_run_id == result.agent_run_id
    assert response.started_new_run is True
    assert service.called is True


@pytest.mark.asyncio
async def test_append_message_returns_typed_response_without_streaming(
    monkeypatch,
) -> None:
    result = AgentRunStartResult(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        started_new_run=True,
    )
    service = _ConversationService(result)
    uow_factory, _ = _make_uow_factory()
    monkeypatch.setattr(
        conversation_controller, "_build_conversation_service", lambda uow: service
    )
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=allow_all_context()),
    )

    response = await append_message(
        pod_id=uuid4(),
        conversation_id=result.conversation_id,
        data=SimpleNamespace(content="say ok", metadata=None),
        user=SimpleNamespace(id=uuid4()),
        request=SimpleNamespace(),
        uow_factory=uow_factory,
    )

    assert response.conversation_id == result.conversation_id
    assert response.agent_run_id == result.agent_run_id
    assert response.started_new_run is True
    assert service.called is True


@pytest.mark.asyncio
async def test_append_message_reports_joining_an_active_run(monkeypatch) -> None:
    active_run_id = uuid4()
    result = AgentRunStartResult(
        conversation_id=uuid4(),
        agent_run_id=active_run_id,
        started_new_run=False,
    )
    service = _ConversationService(result)
    uow_factory, _ = _make_uow_factory()
    monkeypatch.setattr(
        conversation_controller, "_build_conversation_service", lambda uow: service
    )
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=allow_all_context()),
    )

    response = await append_message(
        pod_id=uuid4(),
        conversation_id=result.conversation_id,
        data=SimpleNamespace(content="steer this run", metadata=None),
        user=SimpleNamespace(id=uuid4()),
        request=SimpleNamespace(),
        uow_factory=uow_factory,
    )

    assert response.agent_run_id == active_run_id
    assert response.started_new_run is False


def _agent_run(*, status: AgentRunStatus, error: str | None = None) -> AgentRun:
    return AgentRun(
        conversation_id=uuid4(),
        status=status,
        agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        started_at=datetime.now(timezone.utc),
        error=error,
    )


@pytest.mark.asyncio
async def test_stream_conversation_replays_terminal_failed_run(monkeypatch) -> None:
    agent_run = _agent_run(status=AgentRunStatus.FAILED, error="provider failed")
    service = _StreamConversationService(agent_run)
    channel_service = _ChannelService(_empty_iterator())
    uow_factory, _ = _make_uow_factory()
    monkeypatch.setattr(
        conversation_controller, "_build_conversation_service", lambda uow: service
    )
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=allow_all_context()),
    )

    response = await stream_conversation(
        pod_id=uuid4(),
        conversation_id=agent_run.conversation_id,
        user=SimpleNamespace(id=uuid4()),
        channel_service=channel_service,
        request=SimpleNamespace(),
        uow_factory=uow_factory,
        agent_run_id=agent_run.id,
    )
    chunks = [chunk async for chunk in response.body_iterator]
    payload = json.loads(chunks[0].removeprefix("data: ").strip())

    assert payload == {
        "type": "error",
        "data": "provider failed",
        "agent_run_id": str(agent_run.id),
    }


@pytest.mark.asyncio
async def test_stream_conversation_forwards_active_run_events(monkeypatch) -> None:
    agent_run = _agent_run(status=AgentRunStatus.RUNNING)

    async def iterator():
        yield {
            "type": "completed",
            "agent_run_id": str(agent_run.id),
            "data": {"status": "COMPLETED"},
        }

    service = _StreamConversationService(agent_run)
    channel_service = _ChannelService(iterator())
    uow_factory, _ = _make_uow_factory()
    monkeypatch.setattr(
        conversation_controller, "_build_conversation_service", lambda uow: service
    )
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=allow_all_context()),
    )

    response = await stream_conversation(
        pod_id=uuid4(),
        conversation_id=agent_run.conversation_id,
        user=SimpleNamespace(id=uuid4()),
        channel_service=channel_service,
        request=SimpleNamespace(),
        uow_factory=uow_factory,
        agent_run_id=agent_run.id,
    )
    chunks = [chunk async for chunk in response.body_iterator]
    payload = json.loads(chunks[0].removeprefix("data: ").strip())

    assert payload["type"] == "completed"
    assert payload["agent_run_id"] == str(agent_run.id)


@pytest.mark.asyncio
async def test_send_message_encodes_a_dead_subscription_as_stream_error(
    monkeypatch,
) -> None:
    """A dead subscription is not a failed run, and must not be named like one.

    `stream_error` is what tells the client to reconnect; `error` is what tells
    it the run is over. Sending the second while the run is still writing left
    the client sitting on a transcript that stopped moving.
    """
    result = AgentRunStartResult(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        started_new_run=True,
    )
    service = _ConversationService(result)
    channel_service = _ChannelService(_failing_iterator())
    uow_factory, _ = _make_uow_factory()
    monkeypatch.setattr(
        conversation_controller, "_build_conversation_service", lambda uow: service
    )
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=allow_all_context()),
    )

    response = await send_message(
        pod_id=uuid4(),
        conversation_id=result.conversation_id,
        data=SimpleNamespace(content="say ok", metadata=None),
        user=SimpleNamespace(id=uuid4()),
        channel_service=channel_service,
        request=SimpleNamespace(),
        uow_factory=uow_factory,
    )
    chunks = [chunk async for chunk in response.body_iterator]
    # The stream opens with the status frame saying whether this message
    # started a run; the transport failure is the one after it.
    payload = json.loads(chunks[-1].removeprefix("data: ").strip())

    assert payload == {
        "type": "stream_error",
        "data": "Realtime stream interrupted. Reconnect to continue.",
        "agent_run_id": str(result.agent_run_id),
    }
    assert channel_service.exited is True


@pytest.mark.asyncio
async def test_send_message_raises_usage_limit_before_stream_starts(
    monkeypatch,
) -> None:
    channel_service = _ChannelService(_empty_iterator())
    service = _ConversationService(
        exc=UsageLimitExceededError("LLM usage limit exceeded for this account")
    )
    uow_factory, _ = _make_uow_factory()
    monkeypatch.setattr(
        conversation_controller, "_build_conversation_service", lambda uow: service
    )
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=allow_all_context()),
    )

    with pytest.raises(UsageLimitExceededError) as exc_info:
        await send_message(
            pod_id=uuid4(),
            conversation_id=uuid4(),
            data=SimpleNamespace(content="say ok", metadata=None),
            user=SimpleNamespace(id=uuid4()),
            channel_service=channel_service,
            request=SimpleNamespace(),
            uow_factory=uow_factory,
        )

    assert exc_info.value.status_code == 429
    assert service.called is True
    assert channel_service.exited is True


@pytest.mark.asyncio
async def test_send_message_cancellation_releases_pubsub_subscription(
    monkeypatch,
) -> None:
    channel_service = _ChannelService(_empty_iterator())
    service = _ConversationService(exc=asyncio.CancelledError())
    uow_factory, _ = _make_uow_factory()
    monkeypatch.setattr(
        conversation_controller, "_build_conversation_service", lambda uow: service
    )
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=allow_all_context()),
    )

    with pytest.raises(asyncio.CancelledError):
        await send_message(
            pod_id=uuid4(),
            conversation_id=uuid4(),
            data=SimpleNamespace(content="say ok", metadata=None),
            user=SimpleNamespace(id=uuid4()),
            channel_service=channel_service,
            request=SimpleNamespace(),
            uow_factory=uow_factory,
        )

    assert channel_service.exited is True


class TestTheStreamSaysWhetherTheMessageStartedARun:
    """`PS-AGENT-015` asks the system to record whether a message joined a run
    already working, "so a person can be told which happened rather than
    watching an apparently unanswered message". `TurnCoordinator.start` decides
    it and `AgentRunStartResult` carries it; only the append route reported it,
    and the primary chat path is the streaming one.
    """

    async def _first_frame(self, monkeypatch, *, started_new_run: bool) -> dict:
        result = AgentRunStartResult(
            conversation_id=uuid4(),
            agent_run_id=uuid4(),
            started_new_run=started_new_run,
        )
        service = _ConversationService(result)
        uow_factory, _ = _make_uow_factory()
        monkeypatch.setattr(
            conversation_controller, "_build_conversation_service", lambda uow: service
        )
        monkeypatch.setattr(
            "app.core.authorization.scope.resolve_pod_context",
            AsyncMock(return_value=allow_all_context()),
        )

        response = await send_message(
            pod_id=uuid4(),
            conversation_id=result.conversation_id,
            data=SimpleNamespace(content="say ok", metadata=None),
            user=SimpleNamespace(id=uuid4()),
            channel_service=_ChannelService(_empty_iterator()),
            request=SimpleNamespace(),
            uow_factory=uow_factory,
        )
        chunks = [chunk async for chunk in response.body_iterator]
        return json.loads(chunks[0].removeprefix("data: ").strip())

    @pytest.mark.asyncio
    async def test_a_message_that_started_a_run_says_so(self, monkeypatch) -> None:
        payload = await self._first_frame(monkeypatch, started_new_run=True)

        assert payload["type"] == "status"
        assert payload["data"] == {"started_new_run": True}

    @pytest.mark.asyncio
    async def test_a_message_that_joined_one_says_so(self, monkeypatch) -> None:
        payload = await self._first_frame(monkeypatch, started_new_run=False)

        assert payload["type"] == "status"
        assert payload["data"] == {"started_new_run": False}


class TestASilentStreamStillSendsSomething:
    """Nothing is published while a tool runs, so a stream can sit silent for
    minutes on a healthy connection -- past the idle timeout intermediaries
    commonly apply. The client then sees a closed socket rather than the
    `stream_error` frame, and the run carries on writing for nobody.
    """

    async def _slow_then_done(self, gate: asyncio.Event):
        await gate.wait()
        yield "data: {}\n\n"

    @pytest.mark.asyncio
    async def test_a_comment_frame_goes_out_while_nothing_happens(self) -> None:
        from app.modules.agent.api.controllers.shared import (
            KEEPALIVE_FRAME,
            with_keepalive,
        )

        gate = asyncio.Event()
        stream = with_keepalive(self._slow_then_done(gate), interval_seconds=0.01)

        assert await anext(stream) == KEEPALIVE_FRAME
        assert await anext(stream) == KEEPALIVE_FRAME

        # And the real frame still arrives: the pull is held across the
        # timeouts, not cancelled and restarted.
        gate.set()
        assert await anext(stream) == "data: {}\n\n"
        await stream.aclose()

    @pytest.mark.asyncio
    async def test_a_busy_stream_gets_no_keepalives(self) -> None:
        from app.modules.agent.api.controllers.shared import (
            KEEPALIVE_FRAME,
            with_keepalive,
        )

        async def _chatty():
            for index in range(3):
                yield f"data: {index}\n\n"

        chunks = [chunk async for chunk in with_keepalive(_chatty())]

        assert KEEPALIVE_FRAME not in chunks
        assert chunks == ["data: 0\n\n", "data: 1\n\n", "data: 2\n\n"]

    @pytest.mark.asyncio
    async def test_the_underlying_failure_is_not_swallowed(self) -> None:
        """A dead subscription must still reach the caller, which is what turns
        it into the `stream_error` frame."""
        from app.modules.agent.api.controllers.shared import with_keepalive

        async def _dies():
            raise RuntimeError("redis pubsub disconnected")
            if False:  # pragma: no cover - makes this an async generator
                yield None

        with pytest.raises(RuntimeError):
            [chunk async for chunk in with_keepalive(_dies())]
