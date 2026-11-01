from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from starlette.exceptions import HTTPException
from starlette.requests import Request

from sandbox_runtime.function.runtime_models import (
    FunctionSchemaSet,
    RunAccepted,
    RuntimeIdentity,
    RuntimeInvocation,
    SchemaInspection,
    TerminalReport,
)
from sandbox_runtime.function.server import (
    FunctionRuntimeService,
    _read_bounded_invocation_body,
    create_app,
)


pytestmark = pytest.mark.asyncio


def _invocation(function_id=None, **updates) -> RuntimeInvocation:
    function_id = function_id or uuid4()
    payload = {
        "input": {"value": 7},
        "config": {"mode": "test"},
        "identity": RuntimeIdentity(
            user_id=uuid4(),
            pod_id=uuid4(),
            function_id=function_id,
            function_name="probe",
        ),
        "lemma_base_url": "https://api.lemma.test",
        "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=30),
    }
    payload.update(updates)
    return RuntimeInvocation(**payload)


def _report() -> TerminalReport:
    return TerminalReport(
        status="completed",
        output_data={"answer": 42},
        stdout="",
        stderr="",
    )


async def test_exact_run_duplicate_reuses_task_across_token_rotation(
    monkeypatch,
) -> None:
    service = FunctionRuntimeService(max_workers=1, max_cached_revisions=1)
    function_id = uuid4()
    run_id = uuid4()
    invocation = _invocation(function_id)
    execute = AsyncMock(return_value=_report())
    monkeypatch.setattr(service, "_execute", execute)
    parameters = {
        "function_id": function_id,
        "revision_hash": f"sha256:{'a' * 64}",
        "run_id": run_id,
        "gateway_url": "https://gateway.lemma.test",
        "invocation": invocation,
    }
    try:
        first = await service.invoke(
            function_token="first-function-token",
            **parameters,
        )
        duplicate = await service.invoke(
            function_token="rotated-equivalent-token",
            **parameters,
        )
    finally:
        await service.close()

    assert first == duplicate
    execute.assert_awaited_once()


async def test_reusing_run_id_for_different_envelope_is_rejected(
    monkeypatch,
) -> None:
    service = FunctionRuntimeService(max_workers=1, max_cached_revisions=1)
    function_id = uuid4()
    run_id = uuid4()
    execute = AsyncMock(return_value=_report())
    monkeypatch.setattr(service, "_execute", execute)
    parameters = {
        "function_token": "function-token",
        "function_id": function_id,
        "revision_hash": f"sha256:{'a' * 64}",
        "run_id": run_id,
        "gateway_url": "https://gateway.lemma.test",
    }
    try:
        await service.invoke(
            invocation=_invocation(function_id, input={"value": 1}),
            **parameters,
        )
        with pytest.raises(ValueError, match="different invocation"):
            await service.invoke(
                invocation=_invocation(function_id, input={"value": 2}),
                **parameters,
            )
    finally:
        await service.close()


async def test_async_accept_returns_after_local_registration(
    monkeypatch,
) -> None:
    service = FunctionRuntimeService(max_workers=1, max_cached_revisions=1)
    function_id = uuid4()
    run_id = uuid4()
    release = asyncio.Event()

    async def execute(**_kwargs):
        await release.wait()
        return _report()

    monkeypatch.setattr(service, "_execute", execute)
    try:
        accepted = await asyncio.wait_for(
            service.accept(
                function_token="function-token",
                function_id=function_id,
                revision_hash=f"sha256:{'a' * 64}",
                run_id=run_id,
                gateway_url="https://gateway.lemma.test",
                invocation=_invocation(function_id),
            ),
            timeout=0.2,
        )
        assert accepted == RunAccepted(run_id=run_id)
        assert not service._runs[run_id].task.done()
        release.set()
        await service._runs[run_id].task
    finally:
        release.set()
        await service.close()


async def test_cancel_matches_function_and_run_without_credential(
    monkeypatch,
) -> None:
    service = FunctionRuntimeService(max_workers=1, max_cached_revisions=1)
    function_id = uuid4()
    run_id = uuid4()
    started = asyncio.Event()

    async def execute(**_kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(service, "_execute", execute)
    await service.accept(
        function_token="function-token",
        function_id=function_id,
        revision_hash=f"sha256:{'a' * 64}",
        run_id=run_id,
        gateway_url="https://gateway.lemma.test",
        invocation=_invocation(function_id),
    )
    await started.wait()
    try:
        assert not await service.cancel(uuid4(), run_id)
        assert await service.cancel(function_id, run_id)
        await asyncio.sleep(0)
        assert service._runs[run_id].task.cancelled()
    finally:
        await service.close()


async def test_schema_inspection_uses_function_token_and_prewarms_worker(
    monkeypatch,
) -> None:
    service = FunctionRuntimeService(max_workers=1, max_cached_revisions=1)
    function_id = uuid4()
    revision_hash = f"sha256:{'a' * 64}"
    root = Path("/tmp/exact-revision")
    artifact_root = AsyncMock(return_value=root)
    inspect = AsyncMock(
        return_value=FunctionSchemaSet(
            input={"type": "object"},
            output={"type": "object"},
            config=None,
        )
    )
    monkeypatch.setattr(service, "_artifact_root", artifact_root)
    monkeypatch.setattr(service._workers, "inspect_schemas", inspect)
    try:
        result = await service.inspect_schemas(
            function_token="delegated-function-token",
            function_id=function_id,
            revision_hash=revision_hash,
            gateway_url="https://gateway.lemma.test",
        )
    finally:
        await service.close()

    assert result.ok
    artifact_root.assert_awaited_once()
    assert artifact_root.await_args.kwargs["function_token"] == (
        "delegated-function-token"
    )
    inspect.assert_awaited_once()
    assert inspect.await_args.kwargs["artifact_root"] == root


async def test_v2_route_forwards_complete_envelope_and_no_run_token() -> None:
    function_id = uuid4()
    run_id = uuid4()
    invocation = _invocation(function_id)
    observed = {}

    class _Runtime:
        async def invoke(self, **kwargs):
            observed.update(kwargs)
            return _report()

        async def close(self):
            return None

    app = create_app(max_workers=1, max_cached_revisions=1)
    app.state.runtime = _Runtime()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://runtime.test",
    ) as client:
        response = await client.post(
            f"/functions/{function_id}/runs/{run_id}",
            headers={
                "Authorization": "Bearer delegated-function-token",
                "If-Match": f'"sha256:{"a" * 64}"',
                "X-Lemma-Gateway-Url": "https://gateway.lemma.test",
            },
            json=invocation.model_dump(mode="json"),
        )

    assert response.status_code == 200
    assert observed["function_token"] == "delegated-function-token"
    assert observed["invocation"] == invocation
    assert "run_token" not in observed


async def test_schema_route_forwards_same_function_token() -> None:
    function_id = uuid4()
    revision_hash = f"sha256:{'b' * 64}"
    observed = {}

    class _Runtime:
        async def inspect_schemas(self, **kwargs):
            observed.update(kwargs)
            return SchemaInspection(
                ok=True,
                schemas=FunctionSchemaSet(
                    input={"type": "object"},
                    output={"type": "object"},
                ),
            )

        async def close(self):
            return None

    app = create_app(max_workers=1, max_cached_revisions=1)
    app.state.runtime = _Runtime()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://runtime.test",
    ) as client:
        response = await client.post(
            f"/functions/{function_id}/schemas",
            headers={
                "Authorization": "Bearer delegated-function-token",
                "If-Match": f'"{revision_hash}"',
                "X-Lemma-Gateway-Url": "https://gateway.lemma.test",
            },
        )

    assert response.status_code == 200
    assert observed["function_token"] == "delegated-function-token"
    assert observed["revision_hash"] == revision_hash


async def test_a_configured_runtime_refuses_every_call_without_its_credential(
    monkeypatch,
) -> None:
    """Another sandbox on the bridge can reach this port. Without the
    per-sandbox credential it can neither run code here nor cancel a run;
    only the readiness probe is open."""

    monkeypatch.setenv("LEMMA_FUNCTION_RUNTIME_TOKEN", "sandbox-credential")
    function_id = uuid4()
    run_id = uuid4()
    invoked: list[str] = []

    class _Runtime:
        async def invoke(self, **kwargs):
            invoked.append("invoke")
            return _report()

        async def cancel(self, *_args):
            invoked.append("cancel")
            return True

        async def close(self):
            return None

    app = create_app(max_workers=1, max_cached_revisions=1)
    assert "LEMMA_FUNCTION_RUNTIME_TOKEN" not in __import__("os").environ, (
        "the credential must not be inherited by the workers that run user code"
    )
    app.state.runtime = _Runtime()
    headers = {
        "Authorization": "Bearer any-function-token",
        "If-Match": f'"sha256:{"a" * 64}"',
        "X-Lemma-Gateway-Url": "https://gateway.lemma.test",
    }
    body = _invocation(function_id).model_dump(mode="json")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        for credential in (None, "wrong", ""):
            extra = {} if credential is None else {"X-Lemma-Runtime-Token": credential}
            run = await client.post(
                f"/functions/{function_id}/runs/{run_id}",
                headers={**headers, **extra},
                json=body,
            )
            assert run.status_code == 401, credential
            schemas = await client.post(
                f"/functions/{function_id}/schemas", headers={**headers, **extra}
            )
            assert schemas.status_code == 401, credential
            cancel = await client.post(
                f"/functions/{function_id}/runs/{run_id}:cancel", headers=extra
            )
            assert cancel.status_code == 401, credential
        assert invoked == []

        assert (await client.get("/healthz")).status_code == 200
        allowed = await client.post(
            f"/functions/{function_id}/runs/{run_id}",
            headers={**headers, "X-Lemma-Runtime-Token": "sandbox-credential"},
            json=body,
        )
        assert allowed.status_code == 200
        cancel = await client.post(
            f"/functions/{function_id}/runs/{run_id}:cancel",
            headers={"X-Lemma-Runtime-Token": "sandbox-credential"},
        )
        assert cancel.status_code == 202
    assert invoked == ["invoke", "cancel"]


async def test_a_gateway_outside_the_allowlist_is_refused(monkeypatch) -> None:
    """The artifact is fetched from, and verified against, whatever gateway
    the caller names -- so the provider names the only one allowed."""

    monkeypatch.setenv("LEMMA_FUNCTION_GATEWAY_HOSTS", "host.lemma.internal")
    function_id = uuid4()
    app = create_app(max_workers=1, max_cached_revisions=1)

    class _Runtime:
        async def invoke(self, **kwargs):
            return _report()

        async def close(self):
            return None

    app.state.runtime = _Runtime()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        for gateway, status in (
            ("http://attacker.example:8080", 422),
            ("http://host.lemma.internal:8711", 200),
        ):
            response = await client.post(
                f"/functions/{function_id}/runs/{uuid4()}",
                headers={
                    "Authorization": "Bearer any-function-token",
                    "If-Match": f'"sha256:{"a" * 64}"',
                    "X-Lemma-Gateway-Url": gateway,
                },
                json=_invocation(function_id).model_dump(mode="json"),
            )
            assert response.status_code == status, gateway



def _streaming_request(chunks: list[bytes], *, headers=None):
    calls = 0

    async def receive():
        nonlocal calls
        index = calls
        calls += 1
        if index >= len(chunks):
            return {"type": "http.disconnect"}
        return {
            "type": "http.request",
            "body": chunks[index],
            "more_body": index < len(chunks) - 1,
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/functions/test/runs/test",
            "headers": headers or [],
        },
        receive,
    )
    return request, lambda: calls


async def test_invocation_body_limit_rejects_declared_oversize_before_reading(
    monkeypatch,
):
    import sandbox_runtime.function.server as server

    monkeypatch.setattr(server, "_MAX_INPUT_BYTES", 8)
    request, receive_calls = _streaming_request(
        [b"123456789"],
        headers=[(b"content-length", b"9")],
    )

    with pytest.raises(HTTPException) as rejected:
        await _read_bounded_invocation_body(request)

    assert rejected.value.status_code == 413
    assert receive_calls() == 0


async def test_invocation_body_limit_bounds_underreported_streams(monkeypatch):
    import sandbox_runtime.function.server as server

    monkeypatch.setattr(server, "_MAX_INPUT_BYTES", 8)
    request, receive_calls = _streaming_request(
        [b"12345678", b"9", b"unread"],
        headers=[(b"content-length", b"1")],
    )

    with pytest.raises(HTTPException) as rejected:
        await _read_bounded_invocation_body(request)

    assert rejected.value.status_code == 413
    assert receive_calls() == 2


async def test_invocation_body_limit_accepts_exact_boundary(monkeypatch):
    import sandbox_runtime.function.server as server

    monkeypatch.setattr(server, "_MAX_INPUT_BYTES", 8)
    request, _ = _streaming_request([b"1234", b"5678"])

    assert await _read_bounded_invocation_body(request) == b"12345678"


async def test_invocation_body_limit_rejects_malformed_content_length():
    request, receive_calls = _streaming_request(
        [b"{}"],
        headers=[(b"content-length", b"8x")],
    )

    with pytest.raises(HTTPException) as rejected:
        await _read_bounded_invocation_body(request)

    assert rejected.value.status_code == 400
    assert receive_calls() == 0


async def test_oversized_function_invocation_returns_413_without_runtime_work():
    invoked = []

    class _Runtime:
        async def invoke(self, **kwargs):
            invoked.append(kwargs)
            return _report()

        async def close(self):
            return None

    app = create_app(max_workers=1, max_cached_revisions=1)
    app.state.runtime = _Runtime()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        response = await client.post(
            f"/functions/{uuid4()}/runs/{uuid4()}",
            headers={
                "Authorization": "Bearer delegated-function-token",
                "If-Match": f'"sha256:{"a" * 64}"',
                "X-Lemma-Gateway-Url": "https://gateway.lemma.test",
            },
            content=b"x" * (1024 * 1024 + 1),
        )

    assert response.status_code == 413
    assert invoked == []
