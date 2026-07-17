from __future__ import annotations

from typing import Any

from lemma_sdk import Lemma, POD_DEFAULT_AGENT_SELECTOR
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
        return None

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
