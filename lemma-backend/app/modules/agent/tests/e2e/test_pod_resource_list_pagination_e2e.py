from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.function.domain.entities import FunctionRunStatus
from app.modules.function.infrastructure.models import FunctionRunModel
from app.modules.workflow.domain.run import WorkflowRunStatus
from app.modules.workflow.infrastructure.models import WorkflowRunModel

pytestmark = pytest.mark.e2e


async def _create_test_pod(authenticated_client, fixed_test_org) -> str:
    suffix = uuid4().hex[:8]
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"resource-list-{suffix}",
            "description": "Resource list pagination e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_function(authenticated_client, pod_id: str, name: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={"name": name, "description": "pagination test"},
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _create_workflow(authenticated_client, pod_id: str, name: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": name, "description": "pagination test"},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _create_agent(authenticated_client, pod_id: str, name: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "Answer briefly."},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _create_conversation(authenticated_client, pod_id: str, title: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"title": title},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _create_workflow_with_graph(authenticated_client, pod_id: str, name: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/workflows",
        json={
            "name": name,
            "description": "summary contract test",
            "nodes": [
                {
                    "id": "intake",
                    "type": "FORM",
                    "config": {
                        "input_schema": {
                            "type": "object",
                            "properties": {"x": {"type": "string"}},
                        }
                    },
                },
                {"id": "end", "type": "END"},
            ],
            "edges": [{"id": "e1", "source": "intake", "target": "end"}],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


def _item_names(response_payload: dict) -> list[str]:
    return [item["name"] for item in response_payload["items"]]


def _item_titles(response_payload: dict) -> list[str | None]:
    return [item["title"] for item in response_payload["items"]]


@pytest.mark.asyncio
async def test_pod_resource_lists_are_latest_first_and_page_to_older_items(
    authenticated_client,
    fixed_test_org,
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    suffix = uuid4().hex[:8]

    function_names = [f"fn_{suffix}_{index}" for index in range(3)]
    workflow_names = [f"wf_{suffix}_{index}" for index in range(3)]
    agent_names = [f"agent-{suffix}-{index}" for index in range(3)]
    conversation_titles = [f"Conversation {suffix} {index}" for index in range(3)]

    for name in function_names:
        await _create_function(authenticated_client, pod_id, name)
    for name in workflow_names:
        await _create_workflow(authenticated_client, pod_id, name)
    for name in agent_names:
        await _create_agent(authenticated_client, pod_id, name)
    for title in conversation_titles:
        await _create_conversation(authenticated_client, pod_id, title)

    function_page = await authenticated_client.get(
        f"/pods/{pod_id}/functions",
        params={"limit": 2},
        follow_redirects=True,
    )
    assert function_page.status_code == status.HTTP_200_OK, function_page.text
    assert _item_names(function_page.json()) == list(reversed(function_names[1:]))
    function_next = await authenticated_client.get(
        f"/pods/{pod_id}/functions",
        params={"limit": 2, "page_token": function_page.json()["next_page_token"]},
        follow_redirects=True,
    )
    assert _item_names(function_next.json()) == [function_names[0]]

    workflow_page = await authenticated_client.get(
        f"/pods/{pod_id}/workflows",
        params={"limit": 2},
    )
    assert workflow_page.status_code == status.HTTP_200_OK, workflow_page.text
    assert _item_names(workflow_page.json()) == list(reversed(workflow_names[1:]))
    workflow_next = await authenticated_client.get(
        f"/pods/{pod_id}/workflows",
        params={"limit": 2, "page_token": workflow_page.json()["next_page_token"]},
    )
    assert _item_names(workflow_next.json()) == [workflow_names[0]]

    agent_page = await authenticated_client.get(
        f"/pods/{pod_id}/agents",
        params={"limit": 2},
    )
    assert agent_page.status_code == status.HTTP_200_OK, agent_page.text
    assert _item_names(agent_page.json()) == list(reversed(agent_names[1:]))
    agent_next = await authenticated_client.get(
        f"/pods/{pod_id}/agents",
        params={"limit": 2, "page_token": agent_page.json()["next_page_token"]},
    )
    assert _item_names(agent_next.json()) == [agent_names[0]]

    conversation_page = await authenticated_client.get(
        f"/pods/{pod_id}/conversations",
        params={"limit": 2},
    )
    assert conversation_page.status_code == status.HTTP_200_OK, conversation_page.text
    assert _item_titles(conversation_page.json()) == list(
        reversed(conversation_titles[1:])
    )
    conversation_next = await authenticated_client.get(
        f"/pods/{pod_id}/conversations",
        params={
            "limit": 2,
            "page_token": conversation_page.json()["next_page_token"],
        },
    )
    assert _item_titles(conversation_next.json()) == [conversation_titles[0]]


@pytest.mark.asyncio
async def test_run_lists_are_latest_first_page_to_older_and_return_summaries(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    suffix = uuid4().hex[:8]
    function_name = f"fn_runs_{suffix}"
    workflow_name = f"wf_runs_{suffix}"

    function = await _create_function(authenticated_client, pod_id, function_name)
    workflow = await _create_workflow(authenticated_client, pod_id, workflow_name)

    function_run_ids: list[str] = []
    workflow_run_ids: list[str] = []
    now = datetime.now()
    for _ in range(3):
        function_run = FunctionRunModel(
            function_id=UUID(function["id"]),
            user_id=UUID(fixed_test_user["id"]),
            input_data={"large": "input"},
            output_data={"large": "output"},
            status=FunctionRunStatus.COMPLETED,
            error="hidden from list",
            logs="large logs hidden from list",
            started_at=now,
            completed_at=now,
        )
        db_session.add(function_run)
        await db_session.flush()
        function_run_ids.append(str(function_run.id))

        workflow_run = WorkflowRunModel(
            flow_id=UUID(workflow["id"]),
            pod_id=UUID(pod_id),
            user_id=UUID(fixed_test_user["id"]),
            start_type="MANUAL",
            start_payload={"large": "payload"},
            status=WorkflowRunStatus.COMPLETED.value,
            execution_context={"nodes": {"node": {"large": "context"}}},
            step_history=[
                {
                    "step_index": 0,
                    "node_id": "node",
                    "status": "COMPLETED",
                    "started_at": now.isoformat(),
                    "output_data": {"large": "step"},
                }
            ],
            started_at=now,
            completed_at=now,
        )
        db_session.add(workflow_run)
        await db_session.flush()
        workflow_run_ids.append(str(workflow_run.id))
    await db_session.commit()

    function_page = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        params={"limit": 2},
        follow_redirects=True,
    )
    assert function_page.status_code == status.HTTP_200_OK, function_page.text
    function_items = function_page.json()["items"]
    assert [item["id"] for item in function_items] == list(reversed(function_run_ids[1:]))
    assert set(function_items[0]) == {
        "id",
        "function_id",
        "user_id",
        "status",
        "started_at",
        "completed_at",
        "created_at",
    }
    function_next = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        params={"limit": 2, "page_token": function_page.json()["next_page_token"]},
        follow_redirects=True,
    )
    assert [item["id"] for item in function_next.json()["items"]] == [
        function_run_ids[0]
    ]

    function_get = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function_name}/runs/{function_run_ids[-1]}",
        follow_redirects=True,
    )
    assert function_get.status_code == status.HTTP_200_OK, function_get.text
    assert function_get.json()["output_data"] == {"large": "output"}
    assert function_get.json()["logs"] == "large logs hidden from list"

    workflow_page = await authenticated_client.get(
        f"/pods/{pod_id}/workflows/{workflow_name}/runs",
        params={"limit": 2},
    )
    assert workflow_page.status_code == status.HTTP_200_OK, workflow_page.text
    workflow_items = workflow_page.json()["items"]
    assert [item["id"] for item in workflow_items] == list(reversed(workflow_run_ids[1:]))
    assert "execution_context" not in workflow_items[0]
    assert "step_history" not in workflow_items[0]
    workflow_next = await authenticated_client.get(
        f"/pods/{pod_id}/workflows/{workflow_name}/runs",
        params={"limit": 2, "page_token": workflow_page.json()["next_page_token"]},
    )
    assert [item["id"] for item in workflow_next.json()["items"]] == [
        workflow_run_ids[0]
    ]

    workflow_get = await authenticated_client.get(
        f"/pods/{pod_id}/workflow-runs/{workflow_run_ids[-1]}",
    )
    assert workflow_get.status_code == status.HTTP_200_OK, workflow_get.text
    assert workflow_get.json()["execution_context"] == {"node": {"large": "context"}}
    assert workflow_get.json()["step_history"][0]["output_data"] == {"large": "step"}


@pytest.mark.asyncio
async def test_resource_lists_omit_heavy_fields_but_get_returns_them(
    authenticated_client,
    fixed_test_org,
):
    """List endpoints return lean summaries (derived stats only); the heavy
    single-resource fields are reachable only via GET-single."""
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    suffix = uuid4().hex[:8]

    # --- workflows: graph replaced by node_count/node_types in lists --------- #
    wf = await _create_workflow_with_graph(
        authenticated_client, pod_id, f"wf-{suffix}"
    )
    wf_list = await authenticated_client.get(f"/pods/{pod_id}/workflows")
    assert wf_list.status_code == status.HTTP_200_OK, wf_list.text
    wf_item = next(i for i in wf_list.json()["items"] if i["name"] == wf["name"])
    assert "nodes" not in wf_item and "edges" not in wf_item and "start" not in wf_item
    assert wf_item["node_count"] == 2
    assert set(wf_item["node_types"]) == {"FORM", "END"}
    assert "allowed_actions" in wf_item

    wf_get = await authenticated_client.get(
        f"/pods/{pod_id}/workflows/{wf['name']}"
    )
    assert wf_get.status_code == status.HTTP_200_OK, wf_get.text
    assert len(wf_get.json()["nodes"]) == 2 and "edges" in wf_get.json()

    # --- agents: instruction/schemas only on GET ---------------------------- #
    await _create_agent(authenticated_client, pod_id, f"agent-{suffix}")
    agent_list = await authenticated_client.get(f"/pods/{pod_id}/agents")
    assert agent_list.status_code == status.HTTP_200_OK, agent_list.text
    agent_item = agent_list.json()["items"][0]
    for heavy in ("instruction", "input_schema", "output_schema", "agent_runtime"):
        assert heavy not in agent_item, heavy
    assert "toolsets" in agent_item and "allowed_actions" in agent_item

    agent_get = await authenticated_client.get(
        f"/pods/{pod_id}/agents/{agent_item['name']}"
    )
    assert agent_get.status_code == status.HTTP_200_OK, agent_get.text
    assert "instruction" in agent_get.json()

    # --- datastore tables: columns replaced by column_count in lists -------- #
    create_table = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": f"tbl_{suffix}",
            "columns": [
                {"name": "title", "type": "TEXT"},
                {"name": "amount", "type": "INTEGER"},
            ],
        },
        follow_redirects=True,
    )
    assert create_table.status_code == status.HTTP_201_CREATED, create_table.text
    table_list = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables"
    )
    assert table_list.status_code == status.HTTP_200_OK, table_list.text
    table_item = table_list.json()["items"][0]
    assert "columns" not in table_item and "config" not in table_item
    assert table_item["column_count"] >= 2
    assert "allowed_actions" in table_item

    table_get = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table_item['name']}"
    )
    assert table_get.status_code == status.HTTP_200_OK, table_get.text
    assert isinstance(table_get.json()["columns"], list)

    # --- functions: input/output/config schemas only on GET ----------------- #
    fn = await _create_function(authenticated_client, pod_id, f"fn_{suffix}")
    fn_list = await authenticated_client.get(
        f"/pods/{pod_id}/functions", follow_redirects=True
    )
    assert fn_list.status_code == status.HTTP_200_OK, fn_list.text
    fn_item = next(i for i in fn_list.json()["items"] if i["name"] == fn["name"])
    for heavy in ("input_schema", "output_schema", "config_schema", "code"):
        assert heavy not in fn_item, heavy
    assert "allowed_actions" in fn_item

    fn_get = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{fn['name']}", follow_redirects=True
    )
    assert fn_get.status_code == status.HTTP_200_OK, fn_get.text
    assert "input_schema" in fn_get.json() and "output_schema" in fn_get.json()
