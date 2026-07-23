from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from agentbox.function_runtime.runtime_models import TerminalReport
from agentbox.function_runtime.server import FunctionRuntimeService


pytestmark = pytest.mark.asyncio


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
