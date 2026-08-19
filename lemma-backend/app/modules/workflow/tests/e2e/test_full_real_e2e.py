"""Fully-real end-to-end workflow tests.

These run the COMPLETE stack in one process via the ``full_stack`` fixture:
the API, the embedded streaq worker, and the scheduler, wired to a real local
Docker sandbox manager and the Fireworks-backed ``system:lemma`` agent
runtime. Nothing is simulated — datastore writes and webhook posts flow through
Redis to the embedded worker, FUNCTION nodes execute in real Docker
containers, and AGENT nodes make real Fireworks LLM calls.

Gated behind the ``provider`` marker (real third-party creds) and ``workspace``
(Docker). Run with:

    LEMMA_RUN_PROVIDER_E2E=1 uv run pytest \
        app/modules/workflow/tests/e2e/test_full_real_e2e.py -m e2e

The fixture skips automatically when the Fireworks credential is absent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e_authz import auth_headers, signup_user

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.provider]

# Real functions + real LLM + Docker cold start -- give the flow room. The
# poll interval doesn't need the same headroom: each poll is a cheap local
# status GET, not a call into Docker or Fireworks, so it's tightened
# independently of the timeout.
FULL_REAL_TIMEOUT = 240.0
POLL_INTERVAL = 0.15


# --------------------------------------------------------------------------- #
# Control-plane helpers (all driven against the standalone server)
# --------------------------------------------------------------------------- #



def _returns_async(fn):
    """Adapt a sync stub to the now-async ``WebhookVerifier.verify`` port.

    The port became a coroutine so implementations are forced to offload the
    blocking Composio SDK off the event loop. A sync stub still type-checks as
    a callable, so without this the route awaits a dict, raises, and answers
    403 -- which is what these tests started doing.
    """

    async def _call(*args, **kwargs):
        return fn(*args, **kwargs)

    return _call


async def _create_org(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/organizations",
        json={"name": f"Full Real Org {uuid4().hex[:6]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_pod(client: httpx.AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"Full Real Pod {uuid4().hex[:6]}",
            "description": "Full-real e2e pod",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_event_table(client: httpx.AsyncClient, pod_id: str, table: str) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "table_name": table,
            "primary_key_column": "id",
            "columns": [
                {"name": "source", "type": "TEXT"},
                {"name": "merchants", "type": "TEXT"},
            ],
            "enable_rls": True,
        },
    )
    assert response.status_code in {200, 201}, response.text


async def _create_function(
    client: httpx.AsyncClient,
    pod_id: str,
    *,
    name: str,
    code: str,
    function_type: str = "API",
) -> str:
    response = await client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": name,
            "description": name,
            "type": function_type,
            "code": code,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["name"]


def _plan_function_code(name: str) -> str:
    # Reads the inserted record's `merchants` CSV from start.payload and fans it
    # out into structured line items the loop will process.
    return f'''#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: {name}

from typing import Any
from pydantic import BaseModel
from lemma_sdk import FunctionContext


class InputModel(BaseModel):
    merchants: str


class OutputModel(BaseModel):
    items: list[dict[str, Any]]
    label: str


async def {name}(ctx: FunctionContext, data: InputModel) -> OutputModel:
    names = [p.strip() for p in data.merchants.split(",") if p.strip()]
    items = [dict(merchant=n, amount=float(10 + i)) for i, n in enumerate(names)]
    return OutputModel(items=items, label=f"{{len(items)}} expenses to review")
'''


def _record_function_code(name: str) -> str:
    return f'''#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: {name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext


class InputModel(BaseModel):
    merchant: str
    amount: float


class OutputModel(BaseModel):
    merchant: str
    amount: float
    recorded: bool


async def {name}(ctx: FunctionContext, data: InputModel) -> OutputModel:
    return OutputModel(merchant=data.merchant, amount=data.amount, recorded=True)
'''


async def _create_agent(client: httpx.AsyncClient, pod_id: str) -> str:
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"reviewer_{uuid4().hex[:6]}",
            "description": "Summarizes an expense batch for a human reviewer.",
            "instruction": (
                "You summarize an expense batch. Respond with a single short "
                "sentence in the `summary` field describing what needs review."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"label": {"type": "string"}},
            },
            "output_schema": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            "agent_runtime": {"profile_id": "system:lemma"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["name"]


async def _create_workflow(
    client: httpx.AsyncClient,
    pod_id: str,
    *,
    name: str,
    start: dict,
    nodes: list[dict],
    edges: list[dict],
) -> dict:
    create = await client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": name, "start": start, "mode": "GLOBAL"},
    )
    assert create.status_code == 201, create.text
    workflow_name = create.json()["name"]
    graph = await client.put(
        f"/pods/{pod_id}/workflows/{workflow_name}/graph",
        json={"start": start, "nodes": nodes, "edges": edges},
    )
    assert graph.status_code == 200, graph.text
    return graph.json()


async def _create_schedule(client: httpx.AsyncClient, pod_id: str, payload: dict) -> dict:
    response = await client.post(f"/pods/{pod_id}/schedules", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _runs(client: httpx.AsyncClient, pod_id: str, workflow_name: str) -> list[dict]:
    response = await client.get(f"/pods/{pod_id}/workflows/{workflow_name}/runs")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _run(client: httpx.AsyncClient, pod_id: str, run_id: str) -> dict:
    response = await client.get(f"/pods/{pod_id}/workflow-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _wait_for_triggered_run(
    client: httpx.AsyncClient,
    pod_id: str,
    workflow_name: str,
    predicate,
    label: str,
    *,
    timeout: float = FULL_REAL_TIMEOUT,
) -> dict:
    async def probe() -> dict | None:
        runs = await _runs(client, pod_id, workflow_name)
        return await _run(client, pod_id, runs[0]["id"]) if runs else None

    # `probe` returns None until the trigger has produced a run at all -- done
    # and fail_fast both no-op on that, same as the original loop's `if runs:`
    # guard. fail_fast mirrors the original's own fail-fast on "FAILED": every
    # caller waits for WAITING/COMPLETED, never FAILED itself.
    last = await eventually(
        label=label,
        probe=probe,
        done=lambda run: run is not None and predicate(run),
        fail_fast=lambda run: (
            f"status=FAILED error={run.get('error')!r}"
            if run is not None and run.get("status") == "FAILED"
            else None
        ),
        timeout_seconds=timeout,
        interval_seconds=POLL_INTERVAL,
    )
    assert last is not None
    return last


def _graph(*, agent_name: str, plan_fn: str, record_fn: str) -> tuple[list[dict], list[dict]]:
    nodes = [
        {
            "id": "plan",
            "type": "FUNCTION",
            "label": "Plan expenses",
            "config": {
                "function_name": plan_fn,
                "input_mapping": {
                    "merchants": {"type": "expression", "value": "start.payload.merchants"}
                },
            },
        },
        {
            "id": "summarize",
            "type": "AGENT",
            "label": "Summarize for reviewer",
            "config": {
                "agent_name": agent_name,
                "input_mapping": {
                    "label": {"type": "expression", "value": "plan.label"}
                },
            },
        },
        {
            "id": "approve",
            "type": "FORM",
            "label": "Reviewer approval",
            "config": {
                "input_schema": {
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}},
                    "required": ["approved"],
                }
            },
        },
        {
            "id": "route",
            "type": "DECISION",
            "label": "Approved?",
            "config": {
                "rules": [
                    {"condition": "approve.approved == `true`", "next_node_id": "each"}
                ]
            },
        },
        {
            "id": "each",
            "type": "LOOP",
            "label": "Each expense",
            "config": {
                "items_path": "plan.items",
                "item_var_name": "line",
                "child_node_id": "record",
            },
        },
        {
            "id": "record",
            "type": "FUNCTION",
            "label": "Record expense",
            "config": {
                "function_name": record_fn,
                "input_mapping": {
                    "merchant": {"type": "expression", "value": "loop.line.merchant"},
                    "amount": {"type": "expression", "value": "loop.line.amount"},
                },
            },
        },
        {"id": "end", "type": "END", "label": "Done"},
    ]
    edges = [
        {"id": "e1", "source": "plan", "target": "summarize"},
        {"id": "e2", "source": "summarize", "target": "approve"},
        {"id": "e3", "source": "approve", "target": "route"},
        {"id": "e4", "source": "route", "target": "end"},
        {"id": "e5", "source": "each", "target": "end"},
        {"id": "e6", "source": "record", "target": "each"},
    ]
    return nodes, edges


# --------------------------------------------------------------------------- #
# The crown-jewel test
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_datastore_trigger_runs_full_real_workflow(full_stack):
    """A datastore INSERT autonomously drives a realistic workflow end to end:
    real FUNCTION (Docker) -> real AGENT (Fireworks) -> human FORM approval ->
    DECISION -> LOOP -> real FUNCTION per item -> END.
    """
    base = full_stack["host_base_url"]
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        user = await signup_user(client, "full-real")
        client.headers.update(auth_headers(user))

        org_id = await _create_org(client)
        pod_id = await _create_pod(client, org_id)
        table = "expense_events"
        await _create_event_table(client, pod_id, table)

        plan_name = f"plan_{uuid4().hex[:6]}"
        plan_fn = await _create_function(
            client, pod_id, name=plan_name, code=_plan_function_code(plan_name)
        )
        record_name = f"record_{uuid4().hex[:6]}"
        record_fn = await _create_function(
            client, pod_id, name=record_name, code=_record_function_code(record_name)
        )
        agent_name = await _create_agent(client, pod_id)

        nodes, edges = _graph(
            agent_name=agent_name, plan_fn=plan_fn, record_fn=record_fn
        )
        workflow = await _create_workflow(
            client,
            pod_id,
            name=f"expense-review-{uuid4().hex[:6]}",
            start={
                "type": "DATASTORE_EVENT",
                "config": {"table_name": table, "operations": ["INSERT"]},
            },
            nodes=nodes,
            edges=edges,
        )
        await _create_schedule(
            client,
            pod_id,
            {
                "schedule_type": "DATASTORE",
                "workflow_name": workflow["name"],
                "config": {"table_name": table, "operations": ["INSERT"]},
            },
        )

        # Trigger: insert a record. The embedded worker picks up the datastore
        # event, runs the plan FUNCTION in Docker, runs the AGENT on Fireworks,
        # and suspends on the human approval form.
        record = await client.post(
            f"/pods/{pod_id}/datastore/tables/{table}/records",
            json={"data": {"source": "full-real", "merchants": "Uber,Delta,AWS"}},
        )
        assert record.status_code == 201, record.text

        waiting = await _wait_for_triggered_run(
            client,
            pod_id,
            workflow["name"],
            lambda r: r["status"] == "WAITING"
            and (r.get("active_wait") or {}).get("wait_type") == "HUMAN",
            "human approval after real function + agent",
        )
        run_id = waiting["id"]
        assert waiting["start_type"] == "DATASTORE_EVENT"
        assert waiting["execution_context"]["start"]["payload"]["merchants"] == "Uber,Delta,AWS"

        # The plan function really executed in Docker.
        plan_out = waiting["execution_context"]["plan"]
        assert len(plan_out["items"]) == 3, plan_out
        assert {i["merchant"] for i in plan_out["items"]} == {"Uber", "Delta", "AWS"}

        # The agent really ran on Fireworks and produced output before the form.
        summarize_out = waiting["execution_context"].get("summarize")
        assert summarize_out, f"agent produced no output: {waiting['execution_context']}"

        # Approve the form -> decision -> loop -> per-item real functions -> end.
        submit = await client.post(
            f"/pods/{pod_id}/workflow-runs/{run_id}/form",
            json={"node_id": "approve", "inputs": {"approved": True}},
        )
        assert submit.status_code == 200, submit.text

        completed = await _wait_for_triggered_run(
            client,
            pod_id,
            workflow["name"],
            lambda r: r["status"] == "COMPLETED",
            "workflow completion after approval",
        )

        history = {step["node_id"] for step in completed["step_history"]}
        for expected in ["plan", "summarize", "approve", "route", "each", "record", "end"]:
            assert expected in history, f"{expected} missing from {sorted(history)}"

        loop_out = completed["execution_context"]["each"]
        assert loop_out["count"] == 3, loop_out
        assert all(r["recorded"] is True for r in loop_out["results"]), loop_out
        assert {r["merchant"] for r in loop_out["results"]} == {"Uber", "Delta", "AWS"}


# --------------------------------------------------------------------------- #
# Third-party event trigger (webhook) + time-scheduled triggers, fully real
# --------------------------------------------------------------------------- #


def _extract_function_code(name: str) -> str:
    return f'''#input_type_name: InputModel
#output_type_name: OutputModel
#function_name: {name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext


class InputModel(BaseModel):
    subject: str


class OutputModel(BaseModel):
    subject: str
    handled: bool


async def {name}(ctx: FunctionContext, data: InputModel) -> OutputModel:
    return OutputModel(subject=data.subject, handled=True)
'''


def _single_function_graph(extract_fn: str, subject_path: str) -> tuple[list[dict], list[dict]]:
    nodes = [
        {
            "id": "extract",
            "type": "FUNCTION",
            "label": "Handle event",
            "config": {
                "function_name": extract_fn,
                "input_mapping": {
                    "subject": {"type": "expression", "value": subject_path}
                },
            },
        },
        {"id": "end", "type": "END", "label": "Done"},
    ]
    edges = [{"id": "e1", "source": "extract", "target": "end"}]
    return nodes, edges


def _composio_event_payload(subject: str) -> dict:
    provider_id = f"composio-trigger-{uuid4().hex[:8]}"
    return {
        "id": f"composio-log-{uuid4().hex[:8]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "OUTLOOK_MESSAGE_TRIGGER",
        "metadata": {
            "trigger_id": provider_id,
            "connected_account_id": f"connected-{uuid4().hex[:8]}",
            "auth_config_id": f"auth-config-{uuid4().hex[:8]}",
            "user_id": f"composio-user-{uuid4().hex[:8]}",
            "toolkit_slug": "outlook",
        },
        "data": {"source": "composio", "subject": subject},
    }


async def _seed_composio_trigger(db_session) -> None:
    from app.modules.connectors.infrastructure.models.connector import Connector
    from app.modules.connectors.infrastructure.models.connector_trigger import (
        ConnectorTrigger,
    )

    if await db_session.get(Connector, "composio") is None:
        db_session.add(
            Connector(
                id="composio",
                title="Composio",
                kinds=[{"kind": "package", "auth_scheme": "OAUTH2"}],
                is_active=True,
            )
        )
    if await db_session.get(ConnectorTrigger, "OUTLOOK_MESSAGE_TRIGGER") is None:
        db_session.add(
            ConnectorTrigger(
                id="OUTLOOK_MESSAGE_TRIGGER",
                connector_id="composio",
                event_type="outlook.message",
                description="Outlook message",
                config_schema={"type": "object"},
                payload_schema={"type": "object"},
            )
        )
    await db_session.commit()


@pytest.mark.asyncio
async def test_webhook_event_trigger_runs_full_real_workflow(full_stack, db_session, monkeypatch):
    """A third-party webhook POST autonomously starts an EVENT workflow whose
    real FUNCTION consumes the trigger payload (start.payload.*)."""
    await _seed_composio_trigger(db_session)
    base = full_stack["host_base_url"]
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        user = await signup_user(client, "full-real-webhook")
        client.headers.update(auth_headers(user))
        org_id = await _create_org(client)
        pod_id = await _create_pod(client, org_id)

        extract_name = f"extract_{uuid4().hex[:6]}"
        extract_fn = await _create_function(
            client, pod_id, name=extract_name, code=_extract_function_code(extract_name)
        )
        nodes, edges = _single_function_graph(extract_fn, "start.payload.subject")
        workflow = await _create_workflow(
            client,
            pod_id,
            name=f"event-flow-{uuid4().hex[:6]}",
            start={
                "type": "EVENT",
                "config": {
                    "connector_id": "composio",
                    "connector_trigger_id": "OUTLOOK_MESSAGE_TRIGGER",
                    "trigger_config": {"source": "composio"},
                },
            },
            nodes=nodes,
            edges=edges,
        )

        payload = _composio_event_payload("Refund request from customer")
        provider_id = payload["metadata"]["trigger_id"]
        await _create_schedule(
            client,
            pod_id,
            {
                "schedule_type": "WEBHOOK",
                "workflow_name": workflow["name"],
                "config": {"source": "composio", "provider_trigger_id": provider_id},
            },
        )

        monkeypatch.setattr(
            "app.composition.schedule_connectors.ComposioWebhookVerifier.verify",
            _returns_async(
            lambda self, payload_text, headers: {
                "version": "V3",
                "payload": {
                    "id": provider_id,
                    "user_id": payload["metadata"]["user_id"],
                    "toolkit_slug": payload["metadata"]["toolkit_slug"],
                    "trigger_slug": payload["type"],
                    "metadata": {
                        "connected_account": {
                            "id": payload["metadata"]["connected_account_id"],
                            "auth_config_id": payload["metadata"]["auth_config_id"],
                        }
                    },
                    "payload": {**payload["data"]},
                },
                "raw_payload": payload,
            }
        ),
        )

        webhook = await client.post("/webhooks/composio", json=payload)
        assert webhook.status_code == 200, webhook.text

        completed = await _wait_for_triggered_run(
            client,
            pod_id,
            workflow["name"],
            lambda r: r["status"] == "COMPLETED",
            "EVENT workflow completion from webhook",
        )
        assert completed["start_type"] == "EVENT"
        # The real function consumed the trigger payload.
        assert completed["execution_context"]["extract"] == {
            "subject": "Refund request from customer",
            "handled": True,
        }


@pytest.mark.asyncio
async def test_scheduled_once_trigger_runs_full_real_workflow(full_stack):
    """A one-time TIME schedule fires through the embedded scheduler and runs an
    EVENT-free workflow whose real FUNCTION consumes the scheduled payload."""
    base = full_stack["host_base_url"]
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        user = await signup_user(client, "full-real-once")
        client.headers.update(auth_headers(user))
        org_id = await _create_org(client)
        pod_id = await _create_pod(client, org_id)

        extract_name = f"extract_{uuid4().hex[:6]}"
        extract_fn = await _create_function(
            client, pod_id, name=extract_name, code=_extract_function_code(extract_name)
        )
        nodes, edges = _single_function_graph(extract_fn, "start.payload.subject")
        workflow = await _create_workflow(
            client,
            pod_id,
            name=f"once-flow-{uuid4().hex[:6]}",
            start={"type": "SCHEDULED", "config": {"schedule_type": "ONCE"}},
            nodes=nodes,
            edges=edges,
        )

        scheduled_at = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
        await _create_schedule(
            client,
            pod_id,
            {
                "schedule_type": "TIME",
                "workflow_name": workflow["name"],
                "config": {
                    "scheduled_at": scheduled_at,
                    "payload": {"subject": "Nightly batch"},
                },
            },
        )

        completed = await _wait_for_triggered_run(
            client,
            pod_id,
            workflow["name"],
            lambda r: r["status"] == "COMPLETED",
            "SCHEDULED workflow completion from one-time timer",
        )
        assert completed["start_type"] == "SCHEDULED"
        assert completed["execution_context"]["extract"] == {
            "subject": "Nightly batch",
            "handled": True,
        }


# --------------------------------------------------------------------------- #
# JOB-function sleep/wake and AGENT sleep/wake, fully real (real Docker + LLM)
# --------------------------------------------------------------------------- #


async def _start_manual_run(
    client: httpx.AsyncClient, pod_id: str, workflow_name: str
) -> dict:
    response = await client.post(f"/pods/{pod_id}/workflows/{workflow_name}/runs")
    assert response.status_code == 201, response.text
    return response.json()


async def _wait_run(
    client: httpx.AsyncClient,
    pod_id: str,
    run_id: str,
    predicate,
    label: str,
    *,
    timeout: float = FULL_REAL_TIMEOUT,
) -> dict:
    # fail_fast mirrors the original loop's own fail-fast on "FAILED": every
    # caller waits for a COMPLETED shape, never FAILED itself.
    return await eventually(
        label=label,
        probe=lambda: _run(client, pod_id, run_id),
        done=predicate,
        fail_fast=lambda run: (
            f"status=FAILED error={run.get('error')!r}"
            if run.get("status") == "FAILED"
            else None
        ),
        timeout_seconds=timeout,
        interval_seconds=POLL_INTERVAL,
    )


@pytest.mark.asyncio
async def test_manual_job_function_workflow_completes_full_real(full_stack):
    """A JOB function node suspends the workflow, the real function executes in
    Docker, and the real FunctionRunCompletedEvent wakes the run to completion.

    This is the end-to-end proof of the workflow-wake fix and the cold-sandbox
    readiness probe (the first execute must not 502).
    """
    base = full_stack["host_base_url"]
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        user = await signup_user(client, "full-real-job")
        client.headers.update(auth_headers(user))

        org_id = await _create_org(client)
        pod_id = await _create_pod(client, org_id)

        record_name = f"record_{uuid4().hex[:6]}"
        record_fn = await _create_function(
            client,
            pod_id,
            name=record_name,
            code=_record_function_code(record_name),
            function_type="JOB",
        )
        workflow = await _create_workflow(
            client,
            pod_id,
            name=f"job-fn-{uuid4().hex[:6]}",
            start={"type": "MANUAL"},
            nodes=[
                {
                    "id": "record",
                    "type": "FUNCTION",
                    "config": {
                        "function_name": record_fn,
                        "input_mapping": {
                            "merchant": {"type": "literal", "value": "Uber"},
                            "amount": {"type": "literal", "value": 23.5},
                        },
                    },
                },
                {"id": "end", "type": "END"},
            ],
            edges=[{"id": "e1", "source": "record", "target": "end"}],
        )

        run = await _start_manual_run(client, pod_id, workflow["name"])
        # The JOB function suspends the run on a FUNCTION wait while it executes.
        assert run["status"] == "RUNNING"
        assert (run.get("active_wait") or {}).get("wait_type") == "FUNCTION"

        completed = await _wait_run(
            client,
            pod_id,
            run["id"],
            lambda r: r["status"] == "COMPLETED",
            "real JOB function completion waking the workflow",
        )
        assert completed["active_wait"] is None
        assert completed["execution_context"]["record"] == {
            "merchant": "Uber",
            "amount": 23.5,
            "recorded": True,
        }


@pytest.mark.asyncio
async def test_manual_agent_workflow_completes_full_real(full_stack):
    """An AGENT node suspends the workflow; the real Fireworks agent run wakes it
    to completion via AgentRunCompletedEvent."""
    base = full_stack["host_base_url"]
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        user = await signup_user(client, "full-real-agent")
        client.headers.update(auth_headers(user))

        org_id = await _create_org(client)
        pod_id = await _create_pod(client, org_id)
        agent_name = await _create_agent(client, pod_id)

        workflow = await _create_workflow(
            client,
            pod_id,
            name=f"agent-only-{uuid4().hex[:6]}",
            start={"type": "MANUAL"},
            nodes=[
                {
                    "id": "summarize",
                    "type": "AGENT",
                    "config": {
                        "agent_name": agent_name,
                        "input_mapping": {
                            "label": {"type": "literal", "value": "2 expenses to review"}
                        },
                    },
                },
                {"id": "end", "type": "END"},
            ],
            edges=[{"id": "e1", "source": "summarize", "target": "end"}],
        )

        run = await _start_manual_run(client, pod_id, workflow["name"])
        assert run["status"] == "RUNNING"
        assert (run.get("active_wait") or {}).get("wait_type") == "AGENT"

        completed = await _wait_run(
            client,
            pod_id,
            run["id"],
            lambda r: r["status"] == "COMPLETED",
            "real agent run waking the workflow",
        )
        assert completed["active_wait"] is None
        summarize_out = completed["execution_context"].get("summarize")
        assert summarize_out, f"agent produced no output: {completed['execution_context']}"


@pytest.mark.asyncio
async def test_cold_start_api_function_executes_without_502(full_stack):
    """A function executed immediately after sandbox creation must wait for the
    in-sandbox app to be ready and succeed on the first call (no cold-start 502)."""
    base = full_stack["host_base_url"]
    async with httpx.AsyncClient(base_url=base, timeout=60.0) as client:
        user = await signup_user(client, "cold-start-fn")
        client.headers.update(auth_headers(user))

        org_id = await _create_org(client)
        pod_id = await _create_pod(client, org_id)
        name = f"record_{uuid4().hex[:6]}"
        fn = await _create_function(
            client, pod_id, name=name, code=_record_function_code(name)
        )

        response = await client.post(
            f"/pods/{pod_id}/functions/{fn}/runs",
            json={"input_data": {"merchant": "Uber", "amount": 23.5}},
            follow_redirects=True,
        )
        assert response.status_code == 200, response.text
        run = response.json()
        # API functions complete inline; the readiness wait + retry backstop
        # prevent the cold-start 502 on the very first execute.
        assert run["status"] == "COMPLETED", run
        assert run["output_data"] == {
            "merchant": "Uber",
            "amount": 23.5,
            "recorded": True,
        }
