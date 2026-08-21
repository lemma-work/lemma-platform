from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
import pytest

from lemma_sdk import Lemma
from lemma_sdk.config import (
    DEFAULT_CONFIG_PATH,
    get_access_token_from_config,
    load_config,
)
from lemma_sdk.errors import LemmaAPIError
from lemma_sdk.openapi_client.models.agent_toolset import AgentToolset
from lemma_sdk.openapi_client.models.column_schema import ColumnSchema
from lemma_sdk.openapi_client.models.create_agent_request import CreateAgentRequest
from lemma_sdk.openapi_client.models.create_function_request import (
    CreateFunctionRequest,
)
from lemma_sdk.openapi_client.models.create_table_request import CreateTableRequest
from lemma_sdk.openapi_client.models.datastore_data_type import DatastoreDataType
from lemma_sdk.openapi_client.models.function_run_status import FunctionRunStatus
from lemma_sdk.openapi_client.models.function_type import FunctionType
from lemma_sdk.openapi_client.models.manual_workflow_start_input import (
    ManualWorkflowStartInput,
)
from lemma_sdk.openapi_client.models.pod_create_request import PodCreateRequest
from lemma_sdk.openapi_client.models.resource_visibility import ResourceVisibility
from lemma_sdk.openapi_client.models.workflow_create_request import (
    WorkflowCreateRequest,
)
from lemma_sdk.openapi_client.models.workflow_mode import WorkflowMode


DEFAULT_CONNECTOR_BASE_URL = "http://127.0.0.1:8711"


@dataclass
class ScenarioStep:
    name: str
    status: str
    detail: str
    elapsed: float


@dataclass
class ScenarioSummary:
    title: str
    steps: list[ScenarioStep] = field(default_factory=list)

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        except Exception as exc:
            self.steps.append(
                ScenarioStep(name, "FAIL", str(exc), time.perf_counter() - started)
            )
            raise
        else:
            self.steps.append(
                ScenarioStep(name, "PASS", "", time.perf_counter() - started)
            )

    def note(self, name: str, detail: str) -> None:
        self.steps.append(ScenarioStep(name, "INFO", detail, 0.0))

    def skip(self, name: str, detail: str) -> None:
        self.steps.append(ScenarioStep(name, "SKIP", detail, 0.0))

    def render(self) -> str:
        lines = [f"\nLemma SDK connector scenario: {self.title}"]
        for step in self.steps:
            elapsed = f" ({step.elapsed:.2f}s)" if step.elapsed else ""
            detail = f" - {step.detail}" if step.detail else ""
            lines.append(f"  {step.status:<4} {step.name}{elapsed}{detail}")
        return "\n".join(lines)


@pytest.fixture
def scenario_summary() -> Iterator[ScenarioSummary]:
    summary = ScenarioSummary("Customer support pod end-to-end")
    yield summary
    print(summary.render())


def _connector_settings() -> tuple[str, str, bool]:
    if os.getenv("LEMMA_RUN_CONNECTOR", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("Set LEMMA_RUN_CONNECTOR=1 to run real Lemma SDK connector tests.")
    base_url = (
        os.getenv("LEMMA_CONNECTOR_BASE_URL")
        or os.getenv("LEMMA_BASE_URL")
        or DEFAULT_CONNECTOR_BASE_URL
    ).rstrip("/")
    config = load_config(DEFAULT_CONFIG_PATH)
    token = (
        os.getenv("LEMMA_CONNECTOR_TOKEN")
        or os.getenv("LEMMA_TOKEN")
        or get_access_token_from_config(config)
    )
    verify_ssl = os.getenv("LEMMA_SSL_NO_VERIFY", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not token:
        pytest.skip(
            "Set LEMMA_TOKEN or LEMMA_CONNECTOR_TOKEN to run Lemma SDK connector tests."
        )
    return base_url, token, verify_ssl


def _require_api(base_url: str, verify_ssl: bool) -> None:
    try:
        response = httpx.get(f"{base_url}/health", timeout=5.0, verify=verify_ssl)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Lemma API is not reachable at {base_url}: {exc}")


def _function_code(function_name: str) -> str:
    return f"""#input_type_name: TriageInput
#output_type_name: TriageResult
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext


class TriageInput(BaseModel):
    title: str
    priority: str = "normal"


class TriageResult(BaseModel):
    status: str
    routing_queue: str
    confidence: float


async def {function_name}(ctx: FunctionContext, data: TriageInput) -> TriageResult:
    title = data.title.lower()
    queue = "refunds" if "refund" in title else "general-support"
    confidence = 0.93 if data.priority == "high" else 0.71
    return TriageResult(
        status="triaged",
        routing_queue=queue,
        confidence=confidence,
    )
"""


def _wait_for_function_run(pod, function_name: str, run_id: str):
    deadline = time.monotonic() + float(os.getenv("LEMMA_SDK_FUNCTION_TIMEOUT", "45"))
    run = pod.functions.run_get(function_name, run_id)
    while run.status not in {
        FunctionRunStatus.COMPLETED,
        FunctionRunStatus.FAILED,
        FunctionRunStatus.CANCELLED,
    }:
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"Function run {run_id} did not finish; status={run.status}"
            )
        time.sleep(1)
        run = pod.functions.run_get(function_name, run_id)
    return run


@pytest.mark.integration
@pytest.mark.connector
def test_customer_support_pod_real_user_workflow(
    scenario_summary: ScenarioSummary,
) -> None:
    base_url, token, verify_ssl = _connector_settings()
    _require_api(base_url, verify_ssl)

    suffix = uuid4().hex[:8]
    org_name = f"SDK Support Lab {suffix}"
    pod_name = f"support-ops-{suffix}"
    ticket_table = "tickets"
    triage_function = f"triage_ticket_{suffix}"
    support_agent = f"support_concierge_{suffix}"
    workflow_name = f"refund_followup_{suffix}"

    bootstrap = Lemma(base_url=base_url, token=token, verify_ssl=verify_ssl)
    workspace = None
    pod_id: str | None = None

    try:
        with scenario_summary.step("create organization"):
            org = bootstrap.orgs.create(name=org_name)
            scenario_summary.note("organization selected", f"{org.name} ({org.id})")

        workspace = Lemma(
            base_url=base_url,
            token=token,
            org_id=str(org.id),
            verify_ssl=verify_ssl,
        )

        with scenario_summary.step("create pod"):
            pod_response = workspace.pods.create(
                PodCreateRequest(
                    name=pod_name,
                    organization_id=org.id,
                    description="SDK connector test pod for customer support operations.",
                )
            )
            pod_id = str(pod_response.id)
            pod = workspace.pod(pod_id)
            scenario_summary.note("pod selected", f"{pod_response.name} ({pod_id})")

        with scenario_summary.step("create ticket table"):
            pod.tables.create(
                CreateTableRequest(
                    name=ticket_table,
                    columns=[
                        ColumnSchema(
                            name="title",
                            type_=DatastoreDataType.TEXT,
                            required=True,
                        ),
                        ColumnSchema(
                            name="customer_email",
                            type_=DatastoreDataType.TEXT,
                            required=True,
                        ),
                        ColumnSchema(
                            name="priority",
                            type_=DatastoreDataType.ENUM,
                            options=["low", "normal", "high"],
                            default="normal",
                        ),
                        ColumnSchema(
                            name="status",
                            type_=DatastoreDataType.ENUM,
                            options=["new", "triaged", "closed"],
                            default="new",
                        ),
                        ColumnSchema(name="metadata", type_=DatastoreDataType.JSON),
                    ],
                    enable_rls=False,
                )
            )

        with scenario_summary.step("crud ticket records"):
            ticket = pod.table(ticket_table).create(
                {
                    "title": "Refund request for annual plan",
                    "customer_email": "ada@example.com",
                    "priority": "high",
                    "status": "new",
                    "metadata": {"channel": "email", "customer_tier": "enterprise"},
                }
            )
            ticket_id = ticket["id"]
            updated = pod.table(ticket_table).update(ticket_id, {"status": "triaged"})
            assert updated["status"] == "triaged"
            listed = pod.table(ticket_table).list(limit=10)
            assert any(item.to_dict()["id"] == ticket_id for item in listed.items)

        with scenario_summary.step("query ticket table"):
            query = pod.query(
                "select title, status, priority from tickets where priority = 'high' limit 5"
            )
            assert query.total >= 1

        with TemporaryDirectory() as tmp_dir:
            note_path = Path(tmp_dir) / "refund-runbook.md"
            note_path.write_text(
                "# Refund runbook\n\nEscalate high-priority refund requests within 4 hours.\n",
                encoding="utf-8",
            )
            with scenario_summary.step("upload and list pod files"):
                pod.files.create_folder("/support", description="Support team docs")
                uploaded = pod.files.upload(
                    note_path,
                    directory_path="/support",
                    description="Refund handling runbook",
                )
                uploaded_path = (
                    uploaded.to_dict().get("path") or "/support/refund-runbook.md"
                )
                fetched = pod.files.get(uploaded_path)
                assert fetched.to_dict()["path"] == uploaded_path

        with scenario_summary.step("create and execute triage function"):
            pod.functions.create(
                CreateFunctionRequest(
                    name=triage_function,
                    description="Routes tickets into support queues.",
                    code=_function_code(triage_function),
                    type_=FunctionType.API,
                    visibility=ResourceVisibility.POD,
                )
            )
            run = pod.functions.run(
                triage_function,
                {"title": ticket["title"], "priority": ticket["priority"]},
            )
            completed = _wait_for_function_run(pod, triage_function, str(run.id))
            assert completed.status == FunctionRunStatus.COMPLETED
            output = completed.to_dict().get("output_data") or {}
            assert output["routing_queue"] == "refunds"

        with scenario_summary.step("create support agent"):
            pod.agents.create(
                CreateAgentRequest(
                    name=support_agent,
                    description="Customer support concierge for refund triage.",
                    instruction=(
                        "Help support agents summarize tickets and suggest the next "
                        "best action. Be concise and cite ticket fields."
                    ),
                    toolsets=[AgentToolset.WORKSPACE_CLI],
                    visibility=ResourceVisibility.POD,
                )
            )
            assert pod.agents.get(support_agent).name == support_agent

        with scenario_summary.step("create conversation for agent"):
            conversation = pod.conversations.create_for_agent(
                support_agent,
                title="Refund ticket triage",
                metadata={"ticket_id": ticket_id, "scenario": "sdk-connector"},
            )
            assert str(conversation.id)
            messages = pod.conversations.messages(str(conversation.id), limit=10)
            assert messages.items == []

        with scenario_summary.step("create workflow and run"):
            workflow = WorkflowCreateRequest(
                name=workflow_name,
                description="Manual follow-up workflow for refund tickets.",
                mode=WorkflowMode.USER,
                start=ManualWorkflowStartInput(),
                visibility=ResourceVisibility.POD,
            )
            workflow["nodes"] = [{"id": "done", "type": "END", "label": "Done"}]
            workflow["edges"] = []
            pod.workflows.create(workflow)
            run = pod.workflows.create_run(workflow_name)
            assert str(run.id)

        with scenario_summary.step("discover connectors"):
            apps = pod.connectors.apps.list(limit=10)
            scenario_summary.note(
                "connector catalog",
                f"found {len(apps.items)} connector(s)",
            )

    finally:
        if workspace is not None and pod_id is not None:
            try:
                workspace.pods.delete(pod_id)
                scenario_summary.note("cleanup", f"deleted pod {pod_id}")
            except LemmaAPIError as exc:
                scenario_summary.skip("cleanup", f"pod delete failed: {exc}")
            workspace.close()
        bootstrap.close()
