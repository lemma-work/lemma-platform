from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from agentbox.function_runtime.runtime_models import (
    FunctionArtifactManifest,
    FunctionSchemaSet,
    RunAccepted,
    SchemaInspection,
    TerminalReport,
)
from agentbox.function_runtime.server import FunctionRuntimeService, create_app
from agentbox.function_runtime.trace_context import inject_trace_context


pytestmark = pytest.mark.asyncio


async def test_schema_inspection_runs_in_disposable_function_worker(tmp_path) -> None:
    root = tmp_path / "revision"
    root.mkdir()
    (root / "manifest.json").write_text(
        FunctionArtifactManifest(
            runtime_abi="lemma-function-python-3.14-linux-x86_64-1",
            builder_digest="test",
            input_model="Input",
            output_model="Output",
            entrypoint="run",
        ).model_dump_json(),
        encoding="utf-8",
    )
    (root / "function.py").write_text(
        "from typing import Optional\n"
        "from pydantic import BaseModel\n"
        "class Input(BaseModel):\n"
        "    value: int\n"
        "class Output(BaseModel):\n"
        "    note: Optional[str] = None\n"
        "async def run(ctx, data):\n"
        "    return Output()\n",
        encoding="utf-8",
    )

    result = await FunctionRuntimeService._inspect_artifact_schemas(
        root,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )

    assert result.ok is True
    assert result.schemas is not None
    assert result.schemas.input["properties"]["value"]["type"] == "integer"
    assert result.schemas.output["properties"]["note"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]


async def test_schema_route_uses_runtime_compilation_capability() -> None:
    app = create_app(max_workers=1, max_cached_revisions=1)
    function_id = uuid4()
    observed: dict[str, object] = {}

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

    app.state.runtime = _Runtime()
    revision_hash = f"sha256:{'a' * 64}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://runtime.test",
    ) as client:
        response = await client.post(
            f"/functions/{function_id}/schemas",
            headers={
                "Authorization": "Bearer compilation-capability",
                "If-Match": f'"{revision_hash}"',
                "X-Lemma-Gateway-Url": "https://gateway.lemma.test",
            },
        )

    assert response.status_code == 200
    assert response.json()["schemas"]["input"] == {"type": "object"}
    assert observed == {
        "compilation_token": "compilation-capability",
        "function_id": function_id,
        "revision_hash": revision_hash,
        "gateway_url": "https://gateway.lemma.test",
    }


async def test_run_dedup_requires_same_delegated_function_session(
    monkeypatch,
) -> None:
    service = FunctionRuntimeService(max_workers=2, max_cached_revisions=1)
    function_id = uuid4()
    run_id = uuid4()
    calls: list[str] = []

    async def execute(**kwargs) -> TerminalReport:
        calls.append(kwargs["function_token"])
        return TerminalReport(
            status="completed",
            output_data={"ok": True},
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(service, "_execute", execute)
    arguments = {
        "function_id": function_id,
        "revision_hash": f"sha256:{'a' * 64}",
        "run_id": run_id,
        "run_token": "run-control-" + "a" * 32,
        "gateway_url": "https://gateway.lemma.test",
        "input_data": {"value": 1},
    }
    try:
        first = await service.invoke(
            function_token="delegated-session-a",
            **arguments,
        )
        same = await service.invoke(
            function_token="delegated-session-a",
            **arguments,
        )
        with pytest.raises(ValueError, match="different invocation or session"):
            await service.invoke(
                function_token="delegated-session-b",
                **arguments,
            )
        with pytest.raises(ValueError, match="different invocation or session"):
            await service.invoke(
                function_token="delegated-session-a",
                **(arguments | {"run_token": "run-control-" + "z" * 32}),
            )
    finally:
        await service.close()

    assert first == same
    assert calls == ["delegated-session-a"]


async def test_gateway_claim_receives_exact_invocation_identity(monkeypatch) -> None:
    from agentbox.function_runtime import server as server_module

    function_id = uuid4()
    run_id = uuid4()
    input_data = {"value": 9}
    observed: dict[str, object] = {}

    class _Gateway:
        def __init__(self, base_url: str) -> None:
            observed["base_url"] = base_url

        async def claim(self, token: str, **kwargs):
            observed["token"] = token
            observed.update(kwargs)
            raise RuntimeError("stop after claim framing")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(server_module, "GatewayClient", _Gateway)
    service = FunctionRuntimeService(max_workers=1, max_cached_revisions=1)
    with pytest.raises(RuntimeError, match="stop after claim framing"):
        await service.invoke(
            function_token="delegated-function-session",
            function_id=function_id,
            revision_hash=f"sha256:{'b' * 64}",
            run_id=run_id,
            run_token="run-control-" + "b" * 32,
            gateway_url="https://gateway.lemma.test",
            input_data=input_data,
        )
    await service.close()

    assert observed == {
        "base_url": "https://gateway.lemma.test",
        "token": "delegated-function-session",
        "run_id": run_id,
        "revision_hash": f"sha256:{'b' * 64}",
        "input_data": input_data,
    }


async def test_gateway_client_is_reused_until_service_closes(monkeypatch) -> None:
    from agentbox.function_runtime import server as server_module

    created: list[str] = []
    closed: list[str] = []

    class _Gateway:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url
            created.append(base_url)

        async def close(self) -> None:
            closed.append(self.base_url)

    monkeypatch.setattr(server_module, "GatewayClient", _Gateway)
    service = FunctionRuntimeService(max_workers=1, max_cached_revisions=1)

    first = await service._gateway("https://gateway.lemma.test")
    second = await service._gateway("https://gateway.lemma.test")
    other = await service._gateway("https://other-gateway.lemma.test")

    assert first is second
    assert other is not first
    assert created == [
        "https://gateway.lemma.test",
        "https://other-gateway.lemma.test",
    ]
    assert closed == []

    await service.close()

    assert closed == created


async def test_async_accept_returns_after_claim_without_waiting_for_terminal(
    monkeypatch,
) -> None:
    service = FunctionRuntimeService(max_workers=2, max_cached_revisions=1)
    function_id = uuid4()
    run_id = uuid4()
    finish = asyncio.Event()
    executions = 0

    async def execute(**kwargs) -> TerminalReport:
        nonlocal executions
        executions += 1
        await service._mark_accepted(kwargs["run_id"], "c" * 32)
        await finish.wait()
        return TerminalReport(
            status="completed",
            output_data={"ok": True},
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(service, "_execute", execute)
    arguments = {
        "function_token": "delegated-session",
        "function_id": function_id,
        "revision_hash": f"sha256:{'c' * 64}",
        "run_id": run_id,
        "run_token": "c" * 32,
        "gateway_url": "https://gateway.lemma.test",
        "input_data": {"value": 2},
    }
    try:
        accepted = await service.accept(**arguments)
        duplicate = await service.accept(**arguments)
        assert accepted == duplicate
        assert accepted.run_id == run_id
        assert executions == 1
        async with service._lock:
            assert not service._runs[run_id].task.done()
        finish.set()
        result = await service.invoke(**arguments)
    finally:
        await service.close()

    assert result.status == "completed"


async def test_exact_duplicate_retries_after_pre_acceptance_failure(
    monkeypatch,
) -> None:
    service = FunctionRuntimeService(max_workers=2, max_cached_revisions=1)
    function_id = uuid4()
    run_id = uuid4()
    run_token = "retry-control-" + "r" * 32
    attempts = 0

    async def execute(**kwargs) -> TerminalReport:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error = RuntimeError("transient claim failure")
            await service._mark_rejected(kwargs["run_id"], error)
            raise error
        await service._mark_accepted(kwargs["run_id"], run_token)
        return TerminalReport(
            status="completed",
            output_data={"ok": True},
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(service, "_execute", execute)
    arguments = {
        "function_token": "delegated-session",
        "function_id": function_id,
        "revision_hash": f"sha256:{'e' * 64}",
        "run_id": run_id,
        "run_token": run_token,
        "gateway_url": "https://gateway.lemma.test",
        "input_data": {"value": 4},
    }
    try:
        with pytest.raises(RuntimeError, match="transient claim failure"):
            await service.accept(**arguments)
        accepted = await service.accept(**arguments)
        result = await service.invoke(**arguments)
    finally:
        await service.close()

    assert accepted.run_id == run_id
    assert result.status == "completed"
    assert attempts == 2


async def test_cancel_can_win_before_runtime_claim_finishes(monkeypatch) -> None:
    service = FunctionRuntimeService(max_workers=2, max_cached_revisions=1)
    function_id = uuid4()
    run_id = uuid4()
    run_token = "run-control-" + "d" * 32
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def execute(**_kwargs) -> TerminalReport:
        started.set()
        await blocked.wait()
        raise AssertionError("cancelled execution must not continue")

    monkeypatch.setattr(service, "_execute", execute)
    invocation = asyncio.create_task(
        service.invoke(
            function_token="delegated-session",
            function_id=function_id,
            revision_hash=f"sha256:{'d' * 64}",
            run_id=run_id,
            run_token=run_token,
            gateway_url="https://gateway.lemma.test",
            input_data={"value": 3},
        )
    )
    try:
        await started.wait()
        assert await service.cancel(run_id, "wrong-run-token") is False
        assert await service.cancel(run_id, run_token) is True
        with pytest.raises(asyncio.CancelledError):
            await invocation
    finally:
        blocked.set()
        await service.close()


async def test_invocation_extracts_w3c_context_before_starting_runtime_task() -> None:
    app = create_app(max_workers=1, max_cached_revisions=1)
    observed: dict[str, str] = {}

    class _Runtime:
        async def accept(self, **_kwargs):
            inject_trace_context(observed)
            return RunAccepted(run_id=run_id)

    run_id = uuid4()
    function_id = uuid4()
    app.state.runtime = _Runtime()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://runtime.test",
    ) as client:
        response = await client.post(
            f"/functions/{function_id}/runs/{run_id}",
            headers={
                "Authorization": "Bearer delegated-function-token",
                "If-Match": f'"sha256:{"a" * 64}"',
                "X-Lemma-Gateway-Url": "https://gateway.lemma.test",
                "X-Lemma-Run-Token": "r" * 32,
                "Prefer": "respond-async",
                "traceparent": (
                    "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
                ),
            },
            json={"input": {"value": 1}},
        )

    assert response.status_code == 202, response.text
    assert observed == {
        "traceparent": ("00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"),
    }
