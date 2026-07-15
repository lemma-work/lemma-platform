from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib import request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentbox.apps import SandboxAppSpec, sandbox_app
from agentbox.api.lifecycle import release_sandbox_compute
from agentbox.providers.legacy import LegacyRuntimeProviderMixin
from agentbox.providers.models import ManagedSandbox, SandboxEndpoint, SandboxRef
from agentbox.providers.registry import build_provider, register_provider
from agentbox.schemas import (
    RuntimeSessionRequest,
    SandboxEnsureRequest,
    SandboxInternalStatus,
)


@dataclass
class _Response:
    payload: dict[str, object]
    status: int = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class FakeLifecycleProvider(LegacyRuntimeProviderMixin):
    provider_name = "fake-contract"

    def __init__(self) -> None:
        self.resources: dict[str, SandboxInternalStatus] = {}

    async def create(
        self, sandbox_id: str, request: SandboxEnsureRequest
    ) -> SandboxInternalStatus:
        del request
        return self.resources.setdefault(
            sandbox_id,
            SandboxInternalStatus(id=sandbox_id, ready=True, status="RUNNING"),
        )

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        return self.resources[sandbox_id]

    async def list_managed(self) -> list[ManagedSandbox]:
        return [
            ManagedSandbox(
                ref=SandboxRef(sandbox_id=sandbox_id, provider_id=f"fake:{sandbox_id}"),
                status=status,
                instance_id=f"instance:{sandbox_id}",
            )
            for sandbox_id, status in self.resources.items()
        ]

    async def delete(self, sandbox_id: str) -> bool:
        return self.resources.pop(sandbox_id, None) is not None

    async def release(self, sandbox_id: str) -> bool:
        status = self.resources.get(sandbox_id)
        if status is None or not status.ready:
            return False
        self.resources[sandbox_id] = status.model_copy(
            update={"ready": False, "status": "STOPPED"}
        )
        return True

    async def resolve_endpoint(
        self,
        sandbox_id: str,
        app: SandboxAppSpec,
        *,
        protocol: str = "http",
    ) -> SandboxEndpoint:
        del protocol
        assert sandbox_id in self.resources
        return SandboxEndpoint(
            base_url=f"https://{sandbox_id}.example/{app.name}",
            headers={"X-Provider-Token": "secret"},
            websocket_query={"provider_token": "short-lived"},
            websocket_subprotocols=("agentbox",),
            instance_id=f"instance:{sandbox_id}",
        )


def test_lifecycle_provider_contract_is_idempotent_and_inventory_is_owned() -> None:
    provider = FakeLifecycleProvider()

    first = asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    second = asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    inventory = asyncio.run(provider.list_managed())

    assert first is second
    assert inventory[0].ref == SandboxRef("sandbox-1", "fake:sandbox-1")
    assert asyncio.run(provider.delete("sandbox-1")) is True
    assert asyncio.run(provider.delete("sandbox-1")) is False


def test_registry_loads_registered_provider_without_sdk_imports() -> None:
    register_provider("fake-contract", FakeLifecycleProvider)
    assert isinstance(build_provider("fake-contract"), FakeLifecycleProvider)


def test_legacy_runtime_facade_uses_endpoint_auth_headers(monkeypatch) -> None:
    provider = FakeLifecycleProvider()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    seen_headers: dict[str, str] = {}

    def fake_urlopen(req: request.Request, timeout: float):
        del timeout
        seen_headers.update(dict(req.header_items()))
        return _Response(
            {
                "session_id": "session-1",
                "cwd": "/workspace",
                "env_keys": [],
            }
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    response = asyncio.run(
        provider.create_session(
            "sandbox-1",
            "session-1",
            RuntimeSessionRequest(),
        )
    )

    assert response.session_id == "session-1"
    assert seen_headers["X-provider-token"] == "secret"


def test_endpoint_carries_websocket_auth_without_persisting_it() -> None:
    provider = FakeLifecycleProvider()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    endpoint = asyncio.run(
        provider.resolve_endpoint(
            "sandbox-1", sandbox_app("browser"), protocol="websocket"
        )
    )

    assert endpoint.url(protocol="websocket").startswith("wss://")
    assert endpoint.websocket_query == {"provider_token": "short-lived"}
    assert endpoint.websocket_subprotocols == ("agentbox",)


def test_release_capability_falls_back_for_legacy_entry_point_provider() -> None:
    class LegacyProvider:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def delete(self, sandbox_id: str) -> bool:
            self.deleted.append(sandbox_id)
            return True

    provider = LegacyProvider()

    assert asyncio.run(
        release_sandbox_compute(provider, "sandbox-1")  # type: ignore[arg-type]
    )
    assert provider.deleted == ["sandbox-1"]
