from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request

from agentbox.api.fabric import agentbox_error_response, router
from agentbox.domain import AgentBoxError, SandboxProfileRef, StorageKind
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderCreateAmbiguous,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderReadyResult,
    ProviderStorageResult,
)


pytestmark = pytest.mark.asyncio
API_KEY = "agentbox-unit-test-key"


class FakeProvider:
    name = "fake"
    scope = "fake:test"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(self, database: StateDatabase, *, ambiguous: bool = False) -> None:
        self._database = database
        self._ambiguous = ambiguous
        self.create_calls = 0
        self.ready_calls = 0
        self.release_calls = 0
        self.destroy_calls = 0
        self.destroyed_storage: list[str] = []

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        assert self._database.active_units_of_work == 0
        self.create_calls += 1
        if self._ambiguous:
            raise ProviderCreateAmbiguous("provider response was lost")
        storage = request.workspace_storage
        return ProviderCreateResult(
            provider_id=f"provider-{request.allocation_id}",
            provider_instance_id=f"instance-{request.allocation_id}",
            provider_request_id=f"request-{request.allocation_id}",
            workspace_storage=(
                ProviderStorageResult(
                    provider_storage_id=f"volume-{storage.storage_token}",
                    bound_to_allocation=False,
                )
                if storage is not None
                else None
            ),
        )

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        del profile, deadline_at
        assert self._database.active_units_of_work == 0
        self.ready_calls += 1
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def close(self) -> None:
        return None

    async def release_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        del allocation, deadline_at
        assert self._database.active_units_of_work == 0
        self.release_calls += 1

    async def destroy_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        del allocation, deadline_at
        assert self._database.active_units_of_work == 0
        self.destroy_calls += 1

    async def destroy_workspace_storage(
        self,
        provider_storage_id: str,
        *,
        deadline_at: datetime,
    ) -> None:
        del deadline_at
        assert self._database.active_units_of_work == 0
        self.destroyed_storage.append(provider_storage_id)


@pytest_asyncio.fixture
async def api(tmp_path: Path):
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    provider = FakeProvider(database)
    app = FastAPI()
    app.state.sandbox_lifecycle = SandboxLifecycleService(database, provider)
    app.include_router(router)

    @app.exception_handler(AgentBoxError)
    async def handle_agentbox_error(_request: Request, error: AgentBoxError):
        return agentbox_error_response(error)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://agentbox.test"
    ) as client:
        yield client, provider, database
    await database.dispose()


def ensure_body(*, extra: bool = False) -> dict[str, object]:
    body: dict[str, object] = {
        "profile": {
            "name": "workspace-python-v1",
            "digest": f"sha256:{'a' * 64}",
        },
        "admission_class": "interactive",
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
    }
    if extra:
        body["retry_count"] = 3
    return body


async def test_ensure_and_inspect_use_typed_canonical_routes(api):
    client, provider, database = api
    logical_id = "d37d760b-c5d8-49a2-85c0-c08d81dadcc7"
    headers = {"X-API-Key": API_KEY}

    ensured = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=ensure_body(),
    )
    inspected = await client.get(f"/sandboxes/workspace/{logical_id}", headers=headers)

    assert ensured.status_code == 200
    assert inspected.status_code == 200
    assert ensured.json() == inspected.json()
    assert ensured.json()["ready"] is True
    assert "provider_id" not in ensured.text
    assert "allocation_token" not in ensured.text
    assert provider.create_calls == 1
    assert provider.ready_calls == 1
    assert database.active_units_of_work == 0


async def test_ensure_can_revalidate_an_active_provider_allocation(api):
    client, provider, database = api
    logical_id = "d5f4ba6c-27c2-4b07-9eac-b4c8c4b95f83"
    headers = {"X-API-Key": API_KEY}

    created = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=ensure_body(),
    )
    verify_body = ensure_body()
    verify_body["verify_ready"] = True
    verified = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=verify_body,
    )

    assert created.status_code == 200
    assert verified.status_code == 200
    assert verified.json()["allocation_id"] == created.json()["allocation_id"]
    assert verified.json()["allocation_epoch"] == created.json()["allocation_epoch"]
    assert provider.create_calls == 1
    assert provider.ready_calls == 2
    assert database.active_units_of_work == 0


async def test_unknown_fields_and_naive_deadlines_are_rejected(api):
    client, provider, _database = api
    logical_id = "693006b8-712e-44e3-9e90-6fa7e9eb0154"
    headers = {"X-API-Key": API_KEY}

    extra = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=ensure_body(extra=True),
    )
    naive_body = ensure_body()
    naive_body["deadline_at"] = "2026-07-22T12:30:00"
    naive = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=naive_body,
    )

    assert extra.status_code == 422
    assert naive.status_code == 422
    assert provider.create_calls == 0


async def test_ambiguous_create_returns_typed_error_and_is_not_replayed(
    tmp_path: Path,
):
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    provider = FakeProvider(database, ambiguous=True)
    app = FastAPI()
    app.state.sandbox_lifecycle = SandboxLifecycleService(database, provider)
    app.include_router(router)

    @app.exception_handler(AgentBoxError)
    async def handle_agentbox_error(_request: Request, error: AgentBoxError):
        return agentbox_error_response(error)

    transport = httpx.ASGITransport(app=app)
    logical_id = "9cb8bd39-32c3-4cbf-bc6c-69f3e5574fe0"
    async with httpx.AsyncClient(
        transport=transport, base_url="http://agentbox.test"
    ) as client:
        first = await client.put(
            f"/sandboxes/workspace/{logical_id}",
            headers={"X-API-Key": API_KEY},
            json=ensure_body(),
        )
        second = await client.put(
            f"/sandboxes/workspace/{logical_id}",
            headers={"X-API-Key": API_KEY},
            json=ensure_body(),
        )

    assert first.status_code == 202
    assert first.json()["error"]["code"] == "AMBIGUOUS_CREATE"
    assert first.json()["error"]["retry"] == "wait"
    assert first.headers["Retry-After"] == "1"
    assert second.status_code == 202
    assert second.json()["allocation_state"] == "unknown"
    assert provider.create_calls == 1
    assert database.active_units_of_work == 0
    await database.dispose()


async def test_explicit_destroy_remains_permanent(api):
    client, _provider, _database = api
    logical_id = "fd727488-07e2-46fe-a58d-9684c46bb002"
    headers = {"X-API-Key": API_KEY}
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()

    created = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=ensure_body(),
    )
    destroyed = await client.delete(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        params={"deadline_at": deadline},
    )
    recreated = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=ensure_body(),
    )

    assert created.status_code == 200
    assert destroyed.status_code == 204
    assert recreated.status_code == 404
    assert recreated.json()["error"]["code"] == "SANDBOX_NOT_FOUND"


async def test_release_resume_reuses_exact_workspace_and_destroy_removes_storage(api):
    client, provider, database = api
    logical_id = "2933381d-bfc4-497b-9cf0-d78d8aa3c262"
    headers = {"X-API-Key": API_KEY}
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()

    created = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=ensure_body(),
    )
    released = await client.post(
        f"/sandboxes/workspace/{logical_id}:release",
        headers=headers,
        json={"deadline_at": deadline},
    )
    resumed = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=ensure_body(),
    )
    destroyed = await client.delete(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        params={"deadline_at": deadline},
    )
    recreate = await client.put(
        f"/sandboxes/workspace/{logical_id}",
        headers=headers,
        json=ensure_body(),
    )

    assert created.status_code == 200
    assert released.status_code == 200
    assert released.json()["desired_state"] == "released"
    assert released.json()["allocation_state"] == "released"
    assert resumed.status_code == 200
    assert resumed.json()["allocation_id"] == created.json()["allocation_id"]
    assert resumed.json()["allocation_epoch"] == 2
    assert provider.create_calls == 1
    assert provider.ready_calls == 2
    assert provider.release_calls == 1
    assert destroyed.status_code == 204
    assert provider.destroy_calls == 1
    assert len(provider.destroyed_storage) == 1
    assert recreate.status_code == 404
    assert database.active_units_of_work == 0
