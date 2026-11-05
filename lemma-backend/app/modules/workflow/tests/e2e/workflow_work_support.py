"""Workflow runs driven through the work runtime in-process, against real Postgres.

The API starts runs and submits forms as it does in production; the runtime's
own claim and execute run the items those write, with the two outside parties
replaced at their seams: the function sandbox (``function_work_support``) and
the decision provider (a fixed answer).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.infrastructure.work import RetryPolicy, WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.decisions.contracts import (
    Answer,
    DecisionCaller,
    DecisionRequest,
    DecisionResult,
)
from app.modules.function.events.run_deadline import register_function_deadline
from app.modules.function.events.run_work import register_function_run_work
from app.modules.function.tests.e2e.function_work_support import (
    FakeRuntime,
    callback_settler,
    fake_runtime,
    loopback_dispatcher_factory,
    seed_function,
)
from app.modules.pod.infrastructure.models.pod_models import PodMember
from app.modules.workflow.events.run_work import register_run_work
from app.modules.workflow.events.step_jobs import register_step_jobs

CLASSES = [WorkClass.EVENTS, WorkClass.AUTOMATION, WorkClass.DELIVERY]


class FixedAnswer:
    def __init__(self) -> None:
        self.asked = 0
        self.answer = "yes"

    async def decide(
        self, request: DecisionRequest, caller: DecisionCaller
    ) -> DecisionResult:
        del request, caller
        self.asked += 1
        return DecisionResult(
            answers={"answer": Answer(self.answer)}, provider="e2e", model="fixed"
        )


@dataclass
class FlowWork:
    client: AsyncClient
    sessions: object
    registry: WorkRegistry
    runtime: FakeRuntime
    decisions: FixedAnswer
    pod_id: str
    user_id: UUID

    async def drain(self) -> int:
        return await run_until_idle(
            self.sessions,  # type: ignore[arg-type]
            registry=self.registry,
            classes=CLASSES,
        )

    async def scalar(self, sql: str, **params: object) -> object:
        async with self.sessions() as session:  # type: ignore[operator]
            return await session.scalar(text(sql), params)

    async def function(self, mode: str) -> str:
        async with self.sessions() as session:  # type: ignore[operator]
            function = await seed_function(
                session, pod_id=UUID(self.pod_id), user_id=self.user_id, mode=mode
            )
            await session.commit()
            return function.name

    async def member_id(self) -> str:
        async with self.sessions() as session:  # type: ignore[operator]
            member = await session.scalar(
                select(PodMember.id).where(PodMember.pod_id == UUID(self.pod_id))
            )
        return str(member)

    async def workflow(
        self, nodes: list[dict], edges: list[dict], *, limit: int | None = None
    ) -> str:
        name = f"flow-{uuid4().hex[:8]}"
        start = {"type": "MANUAL"}
        created = await self.client.post(
            f"/pods/{self.pod_id}/workflows",
            json={"name": name, "start": start, "mode": "GLOBAL"},
        )
        assert created.status_code == 201, created.text
        graph = await self.client.put(
            f"/pods/{self.pod_id}/workflows/{name}/graph",
            json={"start": start, "nodes": nodes, "edges": edges},
        )
        assert graph.status_code == 200, graph.text
        if limit is not None:
            patched = await self.client.patch(
                f"/pods/{self.pod_id}/workflows/{name}",
                json={"max_concurrent_runs": limit},
            )
            assert patched.status_code == 200, patched.text
        return name

    async def start(self, workflow: str) -> dict:
        response = await self.client.post(
            f"/pods/{self.pod_id}/workflows/{workflow}/runs"
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def run(self, run_id: str) -> dict:
        response = await self.client.get(f"/pods/{self.pod_id}/workflow-runs/{run_id}")
        assert response.status_code == 200, response.text
        return response.json()

    async def submit(self, run_id: str, node_id: str, inputs: dict) -> dict:
        response = await self.client.post(
            f"/pods/{self.pod_id}/workflow-runs/{run_id}/form",
            json={"node_id": node_id, "inputs": inputs},
        )
        assert response.status_code == 200, response.text
        return response.json()


@asynccontextmanager
async def flow_work(
    client: AsyncClient, db_manager, *, org_id: str, user_id: str, cap: int = 32
) -> AsyncIterator[FlowWork]:
    response = await client.post(
        "/pods",
        json={
            "name": f"Flow work {uuid4().hex[:6]}",
            "description": "workflow work e2e",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    async with fake_runtime() as runtime:
        runtime.on_accepted = callback_settler(db_manager.session_factory)
        decisions = FixedAnswer()
        registry = WorkRegistry()
        register_function_run_work(
            registry,
            dispatcher_factory=loopback_dispatcher_factory(runtime.url, cap=cap),
            retry=RetryPolicy(max_attempts=3),
        )
        register_function_deadline(registry)
        register_run_work(registry)
        register_step_jobs(registry, maker=lambda: decisions)
        yield FlowWork(
            client,
            db_manager.session_factory,
            registry,
            runtime,
            decisions,
            response.json()["id"],
            UUID(user_id),
        )
