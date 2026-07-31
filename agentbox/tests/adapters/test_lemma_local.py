from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from uuid import uuid4

import pytest

from agentbox.adapters.docker import RuntimeCredentialSigner
from agentbox.adapters.lemma_local import (
    LemmaLocalAdapterConfig,
    LemmaLocalSandboxAdapter,
)
from agentbox.domain import (
    SandboxCapability,
    SandboxKey,
    SandboxProfileRef,
    WorkloadKind,
)
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderCreateRequest,
    ProviderMetadataEntry,
)
from agentbox.profiles import DockerProfileArtifact, ProfileRegistry, SandboxProfile


pytestmark = pytest.mark.asyncio


def _profile(kind: WorkloadKind) -> SandboxProfile:
    workspace = kind == WorkloadKind.WORKSPACE
    fill = "a" if workspace else "b"
    image_name = "workspace" if workspace else "function"
    return SandboxProfile(
        ref=SandboxProfileRef(
            name=f"{image_name}-python-v1",
            digest=f"sha256:{fill * 64}",
        ),
        workload_kind=kind,
        runtime_abi="test-abi",
        capabilities=frozenset(
            {SandboxCapability.PROCESS}
            if workspace
            else {SandboxCapability.PORT_ACCESS}
        ),
        allowed_roots=("/workspace",) if workspace else ("/tmp",),
        docker=DockerProfileArtifact(
            image=f"ghcr.io/lemma-work/{image_name}@sha256:{fill * 64}",
            command=(),
            readiness_argv=(),
            published_ports=(8080, 4848) if workspace else (8090,),
            runtime_port=8080 if workspace else 8090,
        ),
        e2b=None,
    )


def _adapter() -> tuple[LemmaLocalSandboxAdapter, SandboxProfile, SandboxProfile]:
    workspace = _profile(WorkloadKind.WORKSPACE)
    function = _profile(WorkloadKind.FUNCTION)
    return (
        LemmaLocalSandboxAdapter(
            ProfileRegistry((workspace, function)),
            LemmaLocalAdapterConfig(
                executable=sys.executable,
                callback_required=True,
                callback_url="http://host.lemma.internal:8711",
            ),
            RuntimeCredentialSigner(b"k" * 32),
        ),
        workspace,
        function,
    )


def _create_request(profile: SandboxProfile) -> ProviderCreateRequest:
    key = SandboxKey(profile.workload_kind, uuid4())
    allocation_id = uuid4()
    allocation_token = uuid4()
    return ProviderCreateRequest(
        allocation_id=allocation_id,
        allocation_token=allocation_token,
        key=key,
        profile=profile.ref,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        metadata=(
            ProviderMetadataEntry("managed-by", "agentbox"),
            ProviderMetadataEntry("workload-kind", profile.workload_kind.value),
            ProviderMetadataEntry("allocation-id", str(allocation_id)),
            ProviderMetadataEntry("allocation-token", str(allocation_token)),
            ProviderMetadataEntry("profile-name", profile.ref.name),
            ProviderMetadataEntry("profile-digest", profile.ref.digest),
        ),
        workspace_storage=None,
    )


async def test_workspace_create_uses_new_profile_image_and_private_runtime_token(
    monkeypatch,
) -> None:
    adapter, workspace, _ = _adapter()
    captured: dict[str, object] = {}

    async def request(operation, parameters, *, deadline_at):
        captured.update(
            operation=operation,
            parameters=parameters,
            deadline_at=deadline_at,
        )
        return {
            "provider_id": "container-generation",
            "metadata": dict(parameters["metadata"]),
            "status": {"id": parameters["sandbox_id"], "ready": True},
        }

    monkeypatch.setattr(adapter, "_request", request)
    create = _create_request(workspace)

    result = await adapter.create(create)

    parameters = captured["parameters"]
    assert captured["operation"] == "sandbox.ensure"
    assert parameters["workload_kind"] == "workspace"
    assert parameters["image"] == workspace.docker.image
    assert parameters["runtime_token"]
    assert [app["name"] for app in parameters["apps"]] == ["runtime", "browser"]
    assert parameters["callback"] == {
        "required": True,
        "url": "http://host.lemma.internal:8711",
        "health_path": "/health",
        "timeout_seconds": 30,
    }
    assert result.provider_id.startswith("w-")
    assert result.workspace_storage is not None


async def test_function_create_is_stateless_and_uses_function_profile(
    monkeypatch,
) -> None:
    adapter, _, function = _adapter()
    captured: dict[str, object] = {}

    async def request(operation, parameters, *, deadline_at):
        del operation, deadline_at
        captured.update(parameters)
        return {
            "provider_id": "container-generation",
            "metadata": dict(parameters["metadata"]),
            "status": {"id": parameters["sandbox_id"], "ready": True},
        }

    monkeypatch.setattr(adapter, "_request", request)

    result = await adapter.create(_create_request(function))

    assert captured["workload_kind"] == "function"
    assert captured["image"] == function.docker.image
    assert captured["runtime_token"] is None
    assert [app["name"] for app in captured["apps"]] == ["function"]
    assert result.provider_id.startswith("f-")
    assert result.workspace_storage is None


async def test_workspace_release_quiesces_before_native_stop(monkeypatch) -> None:
    adapter, _, _ = _adapter()
    calls: list[str] = []

    class Runtime:
        async def quiesce(self, *, deadline_at):
            del deadline_at
            calls.append("quiesce")

        async def close(self):
            calls.append("close")

    async def runtime_client(provider_id, *, deadline_at):
        del provider_id, deadline_at
        return Runtime()

    async def mutate(operation, sandbox_id, *, deadline_at):
        del sandbox_id, deadline_at
        calls.append(operation)

    monkeypatch.setattr(adapter, "_runtime_client", runtime_client)
    monkeypatch.setattr(adapter, "_mutate", mutate)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    allocation = ProviderAllocationRef(
        provider_id=f"w-{key.logical_id.hex}",
        provider_instance_id="generation",
        allocation_id=uuid4(),
        allocation_token=uuid4(),
        key=key,
    )

    await adapter.release_allocation(
        allocation,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )

    assert calls == ["quiesce", "close", "sandbox.release"]
