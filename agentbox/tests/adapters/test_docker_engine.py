from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from agentbox.adapters.docker_engine import (
    DockerContainerCreateRequest,
    DockerContainerInspect,
    DockerEngineClient,
    DockerHostConfig,
    DockerRequestAmbiguous,
)


pytestmark = pytest.mark.asyncio


async def test_container_create_uses_typed_engine_http_request():
    observed: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            201,
            json={"Id": "container-123", "Warnings": []},
            request=request,
        )

    engine = DockerEngineClient(transport=httpx.MockTransport(handler))
    deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
    result = await engine.create_container(
        "ab-f-test",
        DockerContainerCreateRequest(
            image="python@sha256:" + "a" * 64,
            command=("sleep", "infinity"),
            labels={"managed-by": "agentbox"},
            host_config=DockerHostConfig(
                memory=1024,
                nano_cpus=1_000_000_000,
                network_mode="lemma-private",
            ),
        ),
        deadline_at=deadline,
    )
    await engine.close()

    assert result.container_id == "container-123"
    assert len(observed) == 1
    request = observed[0]
    assert request.method == "POST"
    assert request.url.path == "/v1.44/containers/create"
    assert request.url.params["name"] == "ab-f-test"
    body = json.loads(request.content)
    assert body["Image"].startswith("python@sha256:")
    assert body["HostConfig"]["CapDrop"] == ["ALL"]
    assert body["HostConfig"]["SecurityOpt"] == ["no-new-privileges:true"]
    assert body["HostConfig"]["NetworkMode"] == "lemma-private"
    assert body["Labels"] == {"managed-by": "agentbox"}


async def test_container_inspect_parses_private_network_attachment():
    inspected = DockerContainerInspect.model_validate(
        {
            "Id": "container-123",
            "State": {"Status": "running", "Running": True, "ExitCode": 0},
            "Config": {"Image": "workspace:dev", "Labels": {}},
            "NetworkSettings": {
                "Ports": {},
                "Networks": {
                    "lemma-private": {"IPAddress": "172.28.0.7"},
                },
            },
        }
    )

    assert (
        inspected.network_settings.networks["lemma-private"].ip_address == "172.28.0.7"
    )


async def test_lost_container_create_response_is_ambiguous():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection lost", request=request)

    engine = DockerEngineClient(transport=httpx.MockTransport(handler))
    deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
    with pytest.raises(DockerRequestAmbiguous):
        await engine.create_container(
            "ab-f-test",
            DockerContainerCreateRequest(
                image="python@sha256:" + "a" * 64,
                labels={"managed-by": "agentbox"},
                host_config=DockerHostConfig(),
            ),
            deadline_at=deadline,
        )
    await engine.close()
