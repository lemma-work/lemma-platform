import json
import asyncio
from contextlib import asynccontextmanager
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
    send_message,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
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


class _ConversationListService:
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
        ctx=SimpleNamespace(),
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
            ctx=SimpleNamespace(),
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
async def test_send_message_encodes_stream_failures_as_sse_errors(monkeypatch) -> None:
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
    payload = json.loads(chunks[0].removeprefix("data: ").strip())

    assert payload == {
        "type": "error",
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
async def test_send_message_cancellation_releases_pubsub_subscription(monkeypatch) -> None:
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
