from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest

from lemma_sdk import POD_DEFAULT_AGENT_SELECTOR, Lemma
from lemma_sdk.errors import LemmaServerError
from lemma_sdk.openapi_client.models.agent_run_start_response import (
    AgentRunStartResponse,
)
from lemma_sdk.openapi_client.models.agent_toolset import AgentToolset
from lemma_sdk.openapi_client.models.approval_decision_response import (
    ApprovalDecisionResponse,
)
from lemma_sdk.openapi_client.models.message_response import MessageResponse
from lemma_sdk.openapi_client.types import UNSET


class StubTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.generated = object()

    def call(self, endpoint, *path_args, body=None, body_model=None, **kwargs):
        self.calls.append(
            {
                "endpoint": endpoint.__name__,
                "path_args": path_args,
                "body": body,
                "body_model": getattr(body_model, "__name__", None),
                "kwargs": kwargs,
            }
        )
        return

    def close(self) -> None:
        pass


def _pod():
    lemma = Lemma(
        token="token",
        base_url="https://api.example.test",
        org_id="11111111-1111-4111-8111-111111111111",
    )
    transport = StubTransport()
    lemma._transport = transport
    pod = lemma.pod("22222222-2222-4222-8222-222222222222")
    pod.conversations._transport = transport
    return pod, transport


def test_create_for_agent_forwards_parent_id_for_subagent_conversations():
    pod, transport = _pod()

    pod.conversations.create_for_agent(
        "reporter",
        title="child run",
        parent_id="99999999-9999-4999-8999-999999999999",
    )

    body = transport.calls[0]["body"]
    assert transport.calls[0]["body_model"] == "CreateConversationRequest"
    assert body["agent_name"] == "reporter"
    assert body["title"] == "child run"
    assert body["parent_id"] == "99999999-9999-4999-8999-999999999999"


def test_create_for_agent_omits_parent_id_when_not_a_subagent():
    pod, transport = _pod()

    pod.conversations.create_for_agent("reporter", title="top level")

    # compact() drops the unset parent_id so top-level conversations stay clean.
    assert "parent_id" not in transport.calls[0]["body"]


def test_list_without_agent_name_lists_across_the_pod():
    pod, transport = _pod()

    pod.conversations.list()

    assert transport.calls[0]["kwargs"]["agent_name"] is UNSET


def test_list_default_uses_canonical_selector():
    pod, transport = _pod()

    pod.conversations.list_default()

    assert POD_DEFAULT_AGENT_SELECTOR == "POD_DEFAULT"
    assert transport.calls[0]["kwargs"]["agent_name"] == "POD_DEFAULT"


def test_flat_message_response_parses_tool_call_without_nested_content():
    message = MessageResponse.from_dict(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "conversation_id": "00000000-0000-0000-0000-000000000002",
            "sequence": 3,
            "role": "assistant",
            "kind": "TOOL_CALL",
            "tool_name": "request_approval",
            "tool_call_id": "tc-1",
            "tool_args": {
                "tool_name": "exec_command",
                "args": {"cmd": "lemma records delete orders --id 42"},
                "title": "Delete order 42",
                "reason": "needs your authority",
            },
            "created_at": "2026-06-15T00:00:00Z",
        }
    )

    assert message.kind.value == "TOOL_CALL"
    assert message.tool_name == "request_approval"
    # The approval card payload lives flat under tool_args, not content.content.
    assert message.tool_args["tool_name"] == "exec_command"
    assert message.tool_args["args"]["cmd"].endswith("--id 42")


def test_approval_decision_response_shape():
    decision = ApprovalDecisionResponse.from_dict(
        {"approval_id": "tc-1", "decision": "APPROVE_ONCE", "status": "resolved"}
    )

    assert decision.approval_id == "tc-1"
    assert decision.decision.value == "APPROVE_ONCE"
    assert decision.status == "resolved"


def test_pod_toolset_enum_includes_pod():
    assert AgentToolset.POD.value == "POD"


def test_retry_returns_typed_start_response():
    pod, transport = _pod()
    start = AgentRunStartResponse(
        conversation_id=UUID("33333333-3333-4333-8333-333333333333"),
        agent_run_id=UUID("44444444-4444-4444-8444-444444444444"),
        started_new_run=True,
    )
    transport.call = MagicMock(return_value=start)

    result = pod.conversations.retry(str(start.conversation_id))

    assert result is start
    assert transport.call.call_args.args[0].__name__.endswith(
        ".agent_conversation_retry"
    )


def test_retry_stream_starts_then_streams_the_returned_run():
    pod, _ = _pod()
    start = AgentRunStartResponse(
        conversation_id=UUID("33333333-3333-4333-8333-333333333333"),
        agent_run_id=UUID("44444444-4444-4444-8444-444444444444"),
        started_new_run=True,
    )
    pod.conversations.retry = MagicMock(return_value=start)
    response = object()
    pod.conversations.stream = MagicMock(return_value=response)

    result = pod.conversations.retry_stream(str(start.conversation_id))

    assert result is response
    pod.conversations.retry.assert_called_once_with(str(start.conversation_id))
    pod.conversations.stream.assert_called_once_with(
        str(start.conversation_id),
        agent_run_id=str(start.agent_run_id),
    )


CONVERSATION_ID = "33333333-3333-4333-8333-333333333333"


def _streaming_pod(handler: Any) -> tuple[Any, list[httpx.Request]]:
    """A pod whose transport answers over a mocked httpx transport, so the real
    streaming path (timeouts, draining, closing) runs."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    lemma = Lemma(token="token", base_url="https://api.example.test")
    lemma._transport.generated.set_httpx_client(
        httpx.Client(
            transport=httpx.MockTransport(record),
            base_url="https://api.example.test",
        )
    )
    return lemma.pod("22222222-2222-4222-8222-222222222222"), seen


def test_send_waits_out_the_whole_run_with_no_read_deadline():
    # The send endpoint streams runtime events until the run completes. Reading
    # them through a 30s-deadline buffered call is what made every agent run
    # longer than half a minute raise LemmaTimeoutError.
    frames = b"event: token\ndata: {}\n\nevent: run.completed\ndata: {}\n\n"
    pod, seen = _streaming_pod(
        lambda _: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=frames
        )
    )

    assert pod.conversations.send(CONVERSATION_ID, "Classify ticket rec-1") is None

    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].extensions["timeout"]["read"] is None


def test_send_does_not_start_a_second_run_when_the_gateway_fails():
    pod, seen = _streaming_pod(lambda _: httpx.Response(504))

    with pytest.raises(LemmaServerError):
        pod.conversations.send(CONVERSATION_ID, "Classify ticket rec-1")

    assert len(seen) == 1
