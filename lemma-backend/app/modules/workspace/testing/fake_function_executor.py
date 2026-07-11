"""Deterministic function-executor/provider routes for the fake AgentBox app."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse


class FakeFunctionExecutorState(Protocol):
    function_behavior: Any
    function_runs: dict[str, dict[str, Any]]
    function_invocations: int

    def configure_function_executor(self, body: dict[str, Any]) -> None: ...

    def next_function_mode(self) -> str: ...


def _function_logs(state: FakeFunctionExecutorState) -> list[dict[str, str]]:
    return [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "stream": "stdout",
            "message": state.function_behavior.log_message,
        }
    ]


def _function_result(
    state: FakeFunctionExecutorState,
    mode: str,
    *,
    function_name: str,
    input_data: dict,
) -> dict[str, Any]:
    if mode == "failed":
        return {
            "status": "failed",
            "output_data": None,
            "error": {
                "name": "ScriptedFunctionError",
                "message": state.function_behavior.error_message,
                "traceback": [],
                "retryable": False,
            },
            "logs": _function_logs(state),
            "code_hash": "fake",
            "duration_ms": 1,
        }
    if mode in {"cancelled", "timeout"}:
        return {
            "status": mode,
            "output_data": None,
            "error": None,
            "logs": _function_logs(state),
            "code_hash": "fake",
            "duration_ms": 1,
        }
    return {
        "status": "completed",
        "output_data": {"echo": input_data, "function": function_name},
        "error": None,
        "logs": _function_logs(state),
        "code_hash": "fake",
        "duration_ms": 1,
    }


def register_fake_function_executor(
    app: FastAPI,
    state: FakeFunctionExecutorState,
) -> None:
    """Attach the fake provider catalog and function-executor HTTP contract."""

    prefix = "/sandboxes/{sandbox_id}/apps/function_executor"

    @app.post("/__test__/function-executor/configure")
    async def configure_function_executor(body: dict[str, Any]) -> dict[str, Any]:
        state.configure_function_executor(body)
        return {"configured": True}

    @app.get("/__test__/function-executor/state")
    async def function_executor_state() -> dict[str, Any]:
        return {
            "invocations": state.function_invocations,
            "runs": state.function_runs,
            "remaining_modes": state.function_behavior.modes,
        }

    @app.get("/v1/models")
    async def fake_openai_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": "mock-safe-model",
                    "object": "model",
                    "architecture": {"input_modalities": ["text", "image"]},
                }
            ],
        }

    @app.get(prefix + "/readiness")
    async def readiness(sandbox_id: str) -> dict[str, bool]:
        del sandbox_id
        return {"ready": True}

    @app.get(prefix + "/health")
    async def health(sandbox_id: str) -> dict[str, str]:
        del sandbox_id
        return {"status": "ok"}

    @app.post(prefix + "/pods/{pod_id}/functions/{function_name}/execute")
    async def execute(
        sandbox_id: str,
        pod_id: str,
        function_name: str,
        body: dict[str, Any],
    ) -> Any:
        del sandbox_id, pod_id
        run_id = str(body.get("run_id"))
        input_data = body.get("input_data") or {}
        mode = state.next_function_mode()
        state.function_invocations += 1
        if state.function_behavior.delay_seconds:
            await asyncio.sleep(state.function_behavior.delay_seconds)
        error_response = _scripted_http_error(mode)
        if error_response is not None:
            return error_response
        state.function_runs[run_id] = {
            "function_name": function_name,
            "input_data": input_data,
            "mode": mode,
            "pending_polls": state.function_behavior.job_pending_polls,
        }
        if body.get("async_job"):
            return {
                "status": "accepted",
                "run_id": run_id,
                "job_id": f"fake-{run_id}",
            }
        return _function_result(
            state,
            mode,
            function_name=function_name,
            input_data=input_data,
        )

    @app.get(prefix + "/runs/{run_id}")
    async def run_status(sandbox_id: str, run_id: str) -> dict[str, Any]:
        del sandbox_id
        run = state.function_runs[run_id]
        pending_polls = int(run["pending_polls"])
        if pending_polls > 0:
            run["pending_polls"] = pending_polls - 1
            return {
                "run_id": run_id,
                "job_id": f"fake-{run_id}",
                "status": "running",
            }
        result = _function_result(
            state,
            str(run["mode"]),
            function_name=str(run["function_name"]),
            input_data=dict(run["input_data"]),
        )
        return {
            "run_id": run_id,
            "job_id": f"fake-{run_id}",
            "status": result["status"],
            "output_data": result["output_data"],
            "error": result["error"],
            "code_hash": result["code_hash"],
            "duration_ms": result["duration_ms"],
        }

    @app.get(prefix + "/runs/{run_id}/logs")
    async def logs(sandbox_id: str, run_id: str) -> dict[str, Any]:
        del sandbox_id
        return {"run_id": run_id, "logs": _function_logs(state)}


def _scripted_http_error(mode: str) -> JSONResponse | None:
    if mode == "http_503":
        return JSONResponse(
            status_code=503,
            content={"detail": "scripted executor unavailable"},
        )
    if mode == "gateway_timeout":
        return JSONResponse(
            status_code=504,
            content={"detail": "scripted ambiguous gateway timeout"},
        )
    if mode == "malformed":
        return JSONResponse(status_code=200, content={"unexpected": True})
    return None
