"""Dynamic function and child-agent tools as real sandboxed runs.

These provision a real function runtime and run child agents, so they belong in
the ``function`` shard — the lane that builds the sandbox images and runs
serially (workers: 1). Running them under the parallel agent shard raced two
real-sandbox cold-provisioners against the endpoint-ready deadline.

They were originally part of the agent hermetic journeys; the helpers they
rely on are shared from that module.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

from app.modules.agent.tests.e2e.test_agent_hermetic_journeys_e2e import (
    _create_pod,
    _create_runtime_profile,
    _send_message,
)
from app.modules.test_support.e2e.scripted_model import script_text, script_tool_call

pytestmark = [pytest.mark.e2e, pytest.mark.real_sandbox, pytest.mark.timeout(300)]


@pytest.mark.asyncio
async def test_dynamic_function_and_agent_tools_create_durable_child_runs(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
    configure_workspace_api_url,
):
    """Invoke granted functions and agents through the generated tool schemas."""
    del worker, configure_workspace_api_url
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]

    function_name = f"callable_{uuid4().hex[:8]}"
    source = f"""#input_type_name: FunctionInput
#output_type_name: FunctionOutput
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class FunctionInput(BaseModel):
    value: str

class FunctionOutput(BaseModel):
    value: str

async def {function_name}(
    ctx: FunctionContext, data: FunctionInput
) -> FunctionOutput:
    return FunctionOutput(value=data.value)
"""
    created_function = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": function_name,
            "description": "Public dynamic callable E2E",
            "code": source,
        },
    )
    assert created_function.status_code == status.HTTP_201_CREATED, (
        created_function.text
    )
    child_name = f"child_{uuid4().hex[:8]}"
    child = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": child_name,
            "instruction": "Return the delegated input briefly.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
        },
    )
    assert child.status_code == status.HTTP_201_CREATED, child.text

    parent_name = f"parent_{uuid4().hex[:8]}"
    parent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": parent_name,
            "instruction": "Invoke the two scripted dynamic tools.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
        },
    )
    assert parent.status_code == status.HTTP_201_CREATED, parent.text
    permissions = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{parent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": function_name,
                    "permission_ids": ["function.execute"],
                },
                {
                    "resource_type": "agent",
                    "resource_name": child_name,
                    "permission_ids": ["agent.execute"],
                },
            ]
        },
    )
    assert permissions.status_code == status.HTTP_200_OK, permissions.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": parent_name,
            "title": "Dynamic callable tools",
            "metadata": {
                "mock_llm_script": [
                    script_tool_call(
                        f"function_{function_name}",
                        {"value": "function input"},
                        tool_call_id="dynamic-function",
                    ),
                    script_tool_call(
                        f"agent_{child_name}",
                        {"input": "delegated child input"},
                        tool_call_id="dynamic-agent",
                    ),
                    script_text("Dynamic function and child agent completed."),
                ]
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Invoke the configured function and child agent.",
    )
    assert events[-1]["type"] == "completed", events

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    tool_returns = [
        item for item in messages.json()["items"] if item["kind"] == "TOOL_RETURN"
    ]
    returns = {item["tool_call_id"]: item["tool_result"] for item in tool_returns}
    tool_names = {item["tool_call_id"]: item["tool_name"] for item in tool_returns}

    # The function tool returns the function's own declared output - the tool
    # adds no envelope of its own (callable_tool_factory returns
    # ``run.output_data``). So the result being exactly FunctionOutput is what
    # proves the real sandboxed function ran and its value round-tripped.
    assert returns["dynamic-function"] == {"value": "function input"}
    assert tool_names["dynamic-function"] == f"function_{function_name}"
    assert "delegated child input" in str(returns["dynamic-agent"])
    assert tool_names["dynamic-agent"] == f"agent_{child_name}"

    children = await authenticated_client.get(
        f"/pods/{pod_id}/conversations",
        params={"parent_id": conversation_id},
    )
    assert children.status_code == status.HTTP_200_OK, children.text
    child_items = children.json()["items"]
    assert len(child_items) == 1
    assert child_items[0]["parent_id"] == conversation_id
    child_detail = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{child_items[0]['id']}"
    )
    assert child_detail.status_code == status.HTTP_200_OK, child_detail.text
    assert child_detail.json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_dynamic_tools_surface_a_failing_function_and_a_schema_carrying_agent(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
    configure_workspace_api_url,
):
    """Two `callable_tool_factory` branches the happy-path dynamic-tools test
    above never reaches.

    A `function_*` tool whose backend run does not complete must surface as a
    graceful tool failure, not a crashed run (`run.status != COMPLETED`). And
    an `agent_*` tool for a child agent that declares its own `input_schema`/
    `output_schema` takes flat schema kwargs rather than the single-string
    fallback, and returns the child's structured dict output as-is.
    """
    del worker, configure_workspace_api_url
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]

    failing_function_name = f"failing_{uuid4().hex[:8]}"
    failing_source = f"""#input_type_name: FunctionInput
#output_type_name: FunctionOutput
#function_name: {failing_function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class FunctionInput(BaseModel):
    value: str

class FunctionOutput(BaseModel):
    value: str

async def {failing_function_name}(
    ctx: FunctionContext, data: FunctionInput
) -> FunctionOutput:
    raise RuntimeError("intentional function failure: " + data.value)
"""
    created_function = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": failing_function_name,
            "description": "Always fails, for the e2e failure branch",
            "code": failing_source,
        },
    )
    assert created_function.status_code == status.HTTP_201_CREATED, (
        created_function.text
    )

    schema_child_name = f"schema_child_{uuid4().hex[:8]}"
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    schema_child = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": schema_child_name,
            "instruction": "Not scripted; the mock model answers unprompted.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
            "input_schema": schema,
            "output_schema": schema,
        },
    )
    assert schema_child.status_code == status.HTTP_201_CREATED, schema_child.text

    parent_name = f"parent_dynamic_edge_{uuid4().hex[:8]}"
    parent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": parent_name,
            "instruction": "Invoke the failing function and the schema agent.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
        },
    )
    assert parent.status_code == status.HTTP_201_CREATED, parent.text
    permissions = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{parent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": failing_function_name,
                    "permission_ids": ["function.execute"],
                },
                {
                    "resource_type": "agent",
                    "resource_name": schema_child_name,
                    "permission_ids": ["agent.execute"],
                },
            ]
        },
    )
    assert permissions.status_code == status.HTTP_200_OK, permissions.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": parent_name,
            "title": "Dynamic tool edge cases",
            "metadata": {
                "mock_llm_script": [
                    script_tool_call(
                        f"function_{failing_function_name}",
                        {"value": "will not complete"},
                        tool_call_id="dynamic-function-failure",
                    ),
                    script_tool_call(
                        f"agent_{schema_child_name}",
                        {"value": "structured input"},
                        tool_call_id="dynamic-agent-schema",
                    ),
                    script_text("Failure and schema-carrying tools completed."),
                ]
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Invoke the failing function and the schema-carrying agent.",
    )
    assert events[-1]["type"] == "completed", events

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    returns = {
        item["tool_call_id"]: item["tool_result"]
        for item in messages.json()["items"]
        if item["kind"] == "TOOL_RETURN"
    }

    failure = returns["dynamic-function-failure"]
    assert failure["success"] is False
    assert "intentional function failure" in failure["error"]

    schema_result = returns["dynamic-agent-schema"]
    assert isinstance(schema_result, dict)
    assert "value" in schema_result
