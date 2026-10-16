"""Agent turns driven through the work runtime in-process, against real Postgres.

The API starts turns as in production (``/messages/append`` writes the run, its
message and its ``agent.turn`` item in one transaction); the runtime's own
claim and execute run them, on a registry holding only the agent's turn and
follow-up kinds plus whatever a test adds -- so nothing else queued in the
database runs as a side effect. The model is the deterministic e2e mock,
scripted per conversation (``mock_llm_script``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import status
from httpx import AsyncClient
from sqlalchemy import text

from functools import cache

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.infrastructure.db.session import (
    async_session_maker,
    create_dedicated_engine,
)
from app.core.infrastructure.work import RetryPolicy, WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.agent.events.run_followup_work import register_run_followup_work
from app.modules.agent.events.turn_work import RunnerFactory, register_turn_work

#: Never dialled: the e2e model is the mock, the profile only needs a URL.
UNUSED_MODEL_BASE_URL = "http://127.0.0.1:9"
TURN_CLASSES = [
    WorkClass.INTERACTIVE,
    WorkClass.AUTOMATION,
    WorkClass.EVENTS,
    WorkClass.DELIVERY,
]


@cache
def runtime_sessions() -> async_sessionmaker:
    """The runtime's own connections, as the worker gives it its own pool.

    Kept off the application pool the turns' own work draws from, so a test
    racing many turns measures the turns and not a pool they share.
    """
    engine = create_dedicated_engine(
        settings.database_url, pool_size=16, logging_name="work-e2e"
    )
    return async_sessionmaker(engine, expire_on_commit=False)


def turn_registry(
    *,
    lease: timedelta | None = None,
    retry: RetryPolicy | None = None,
    runner_factory: RunnerFactory | None = None,
    extra: Sequence[Callable[[WorkRegistry], None]] = (),
) -> WorkRegistry:
    """The agent's turn and follow-up kinds, on a registry of their own."""
    registry = WorkRegistry()
    register_turn_work(
        registry,
        lease=lease,
        retry=retry,
        runner_factory=runner_factory,
        # No surface watches these conversations.
        observer_factory=lambda _uow: None,
    )
    register_run_followup_work(registry)
    for register in extra:
        register(registry)
    return registry


async def drain(
    registry: WorkRegistry,
    *,
    classes: Sequence[WorkClass] = TURN_CLASSES,
    max_rounds: int = 1000,
) -> int:
    return await run_until_idle(
        runtime_sessions(),
        registry=registry,
        classes=list(classes),
        max_rounds=max_rounds,
    )


async def scalar(sql: str, **params: object) -> Any:
    async with async_session_maker() as session:
        return await session.scalar(text(sql), params)


async def write(sql: str, **params: object) -> None:
    """Run one statement and commit it."""
    async with async_session_maker() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def rows(sql: str, **params: object) -> list[Any]:
    async with async_session_maker() as session:
        return list((await session.execute(text(sql), params)).all())


@dataclass
class TurnPod:
    """A pod with a mock-model agent, for driving turns through the API."""

    client: AsyncClient
    pod_id: str
    agent_name: str
    conversations: list[str] = field(default_factory=list)

    async def conversation(
        self, script: list[dict] | None = None, *, metadata: dict | None = None
    ) -> str:
        body: dict[str, object] = {
            "agent_name": self.agent_name,
            "title": "Turn work",
            "metadata": {
                **(metadata or {}),
                **({"mock_llm_script": script} if script is not None else {}),
            },
        }
        response = await self.client.post(
            f"/pods/{self.pod_id}/conversations", json=body
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text
        conversation_id = response.json()["id"]
        self.conversations.append(conversation_id)
        return conversation_id

    async def append(self, conversation_id: str, content: str = "go") -> dict:
        response = await self.client.post(
            f"/pods/{self.pod_id}/conversations/{conversation_id}/messages/append",
            json={"content": content},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()

    async def stop(self, conversation_id: str) -> dict:
        response = await self.client.post(
            f"/pods/{self.pod_id}/conversations/{conversation_id}/stop"
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()

    async def messages(self, conversation_id: str) -> list[dict]:
        response = await self.client.get(
            f"/pods/{self.pod_id}/conversations/{conversation_id}/messages",
            params={"limit": 200},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()["items"]

    async def run_status(self, run_id: str | UUID) -> str | None:
        return await scalar(
            "SELECT status FROM agent_runs WHERE id = :id", id=UUID(str(run_id))
        )


async def runtime_profile(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        f"/organizations/{org_id}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Turn work model {uuid4().hex[:8]}",
            "base_url": f"{UNUSED_MODEL_BASE_URL}/v1",
            "api_key": "turn-work-e2e",
            "default_model_name": "mock-safe-model",
            "model_names": ["mock-safe-model"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def turn_pod(
    client: AsyncClient,
    org_id: str,
    *,
    profile_id: str | None = None,
    toolsets: list[str] | None = None,
) -> TurnPod:
    response = await client.post(
        "/pods",
        json={
            "name": f"Turn work {uuid4().hex[:8]}",
            "description": "agent turn work e2e",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod_id = response.json()["id"]
    profile_id = profile_id or await runtime_profile(client, org_id)
    agent_name = f"turns_{uuid4().hex[:8]}"
    agent = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Follow the script.",
            "agent_runtime": {
                "profile_id": profile_id,
                "model_name": "mock-safe-model",
            },
            "toolsets": toolsets or [],
        },
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text
    return TurnPod(client, pod_id, agent_name)


__all__ = [
    "TURN_CLASSES",
    "TurnPod",
    "drain",
    "rows",
    "runtime_profile",
    "runtime_sessions",
    "scalar",
    "turn_pod",
    "turn_registry",
    "write",
]
