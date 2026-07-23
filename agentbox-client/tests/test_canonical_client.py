from __future__ import annotations

from datetime import datetime, timedelta, timezone
import struct
from uuid import UUID, uuid4

import httpx
import pytest

from agentbox_client import (
    AdmissionClass,
    AgentBoxApiError,
    AgentBoxClient,
    ProfileRef,
    WorkloadKind,
)


pytestmark = pytest.mark.asyncio


def deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def sandbox_body(logical_id: UUID) -> dict[str, object]:
    return {
        "workload_kind": "workspace",
        "logical_id": str(logical_id),
        "desired_state": "present",
        "profile": {
            "name": "workspace-python-v1",
            "digest": f"sha256:{'a' * 64}",
        },
        "allocation_state": "active",
        "allocation_id": str(uuid4()),
        "allocation_epoch": 1,
        "ready": True,
        "operation_id": None,
        "retry_after_ms": None,
    }


async def test_client_uses_typed_workload_route_and_absolute_deadline() -> None:
    logical_id = uuid4()
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=sandbox_body(logical_id))

    http = httpx.AsyncClient(
        base_url="http://agentbox.test", transport=httpx.MockTransport(handler)
    )
    client = AgentBoxClient(
        base_url="http://agentbox.test", api_key="secret", client=http
    )
    result = await client.ensure_sandbox(
        WorkloadKind.WORKSPACE,
        logical_id,
        profile=ProfileRef(name="workspace-python-v1", digest=f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline(),
    )

    assert result.ready is True
    assert captured[0].url.path == f"/sandboxes/workspace/{logical_id}"
    assert captured[0].headers["X-API-Key"] == "secret"
    assert b'"deadline_at"' in captured[0].content
    await http.aclose()


async def test_client_creates_workspace_directory_through_typed_route() -> None:
    logical_id = uuid4()
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    http = httpx.AsyncClient(
        base_url="http://agentbox.test", transport=httpx.MockTransport(handler)
    )
    client = AgentBoxClient(
        base_url="http://agentbox.test", api_key="secret", client=http
    )
    await client.create_directory(
        logical_id,
        "/workspace/c/2026-07-23/example",
        deadline_at=deadline(),
    )

    assert captured[0].method == "PUT"
    assert captured[0].url.path == f"/sandboxes/workspace/{logical_id}/directories"
    assert captured[0].url.params["path"] == "/workspace/c/2026-07-23/example"
    await http.aclose()


async def test_binary_process_output_is_decoded_without_text_coercion() -> None:
    logical_id = uuid4()
    operation_id = uuid4()
    payload = b"\x00stdout\xff"
    frame = struct.pack("!QBI", 7, 1, len(payload)) + payload

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=frame,
            headers={
                "X-AgentBox-Next-Sequence": "8",
                "X-AgentBox-Truncated-Before": "",
                "X-AgentBox-Process-State": "running",
                "X-AgentBox-Exit-Code": "",
            },
        )

    http = httpx.AsyncClient(
        base_url="http://agentbox.test", transport=httpx.MockTransport(handler)
    )
    client = AgentBoxClient(
        base_url="http://agentbox.test", api_key="secret", client=http
    )
    output = await client.read_process_output(
        WorkloadKind.WORKSPACE,
        logical_id,
        operation_id,
        deadline_at=deadline(),
    )

    assert output.chunks[0].sequence == 7
    assert output.chunks[0].data == payload
    assert output.next_sequence == 8
    await http.aclose()


async def test_typed_error_exposes_retry_disposition() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "provider create rate is exhausted",
                    "retry": "wait",
                    "retry_after_ms": 1250,
                    "context": {
                        "kind": "provider",
                        "provider_name": "e2b",
                        "provider_request_id": None,
                    },
                }
            },
        )

    http = httpx.AsyncClient(
        base_url="http://agentbox.test", transport=httpx.MockTransport(handler)
    )
    client = AgentBoxClient(
        base_url="http://agentbox.test", api_key="secret", client=http
    )

    with pytest.raises(AgentBoxApiError) as raised:
        await client.inspect_process(WorkloadKind.FUNCTION, uuid4(), uuid4())

    assert raised.value.code == "RATE_LIMITED"
    assert raised.value.retry.value == "wait"
    assert raised.value.retry_after_ms == 1250
    await http.aclose()


async def test_unknown_dispatch_error_is_not_treated_as_successful_202() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            json={
                "error": {
                    "code": "UNKNOWN_DISPATCH",
                    "message": "the exact process operation is being reconciled",
                    "retry": "wait",
                    "retry_after_ms": 100,
                    "context": {
                        "kind": "process",
                        "operation_id": str(uuid4()),
                    },
                }
            },
        )

    http = httpx.AsyncClient(
        base_url="http://agentbox.test", transport=httpx.MockTransport(handler)
    )
    client = AgentBoxClient(
        base_url="http://agentbox.test", api_key="secret", client=http
    )

    with pytest.raises(AgentBoxApiError) as raised:
        await client.start_process(
            WorkloadKind.FUNCTION,
            uuid4(),
            operation_id=uuid4(),
            argv=("lemma-function-runtime", "execute"),
            cwd="/tmp",
            deadline_at=deadline(),
        )

    assert raised.value.code == "UNKNOWN_DISPATCH"
    assert raised.value.retry.value == "wait"
    await http.aclose()


async def test_file_upload_and_download_use_async_streams() -> None:
    logical_id = uuid4()
    payload = b"a" * 1024 + b"b" * 1024
    uploaded = bytearray()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            async for chunk in request.stream:
                uploaded.extend(chunk)
            return httpx.Response(
                200,
                json={
                    "path": "/workspace/large.bin",
                    "kind": "file",
                    "size_bytes": len(uploaded),
                    "modified_at": datetime.now(timezone.utc).isoformat(),
                    "mode": 0o600,
                    "sha256": None,
                },
            )
        return httpx.Response(200, content=payload)

    async def upload_chunks():
        yield payload[:1024]
        yield payload[1024:]

    http = httpx.AsyncClient(
        base_url="http://agentbox.test", transport=httpx.MockTransport(handler)
    )
    client = AgentBoxClient(
        base_url="http://agentbox.test", api_key="secret", client=http
    )

    written = await client.write_file_stream(
        logical_id,
        "/workspace/large.bin",
        upload_chunks(),
        deadline_at=deadline(),
    )
    async with client.stream_file(
        logical_id,
        "/workspace/large.bin",
        deadline_at=deadline(),
    ) as stream:
        downloaded = b"".join([chunk async for chunk in stream])

    assert bytes(uploaded) == payload
    assert written.size_bytes == len(payload)
    assert downloaded == payload
    await http.aclose()
