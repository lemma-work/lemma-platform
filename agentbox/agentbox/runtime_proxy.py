from __future__ import annotations

import json
import logging
import socket
from urllib import error, request

from fastapi import HTTPException
from pydantic import ValidationError

from agentbox.endpoint_transport import request_endpoint_http
from agentbox.providers.models import TransientGateway
from agentbox.schemas import (
    ExecCommandRequest,
    ExecCommandResponse,
    ListProcessesResponse,
    RuntimeSessionRequest,
    RuntimeSessionResponse,
    WriteStdinRequest,
)
from agentbox.to_thread import run_sync

logger = logging.getLogger(__name__)

_MAX_RUNTIME_ERROR_BODY_LENGTH = 2000


def _truncate_runtime_error_body(value: str) -> str:
    if len(value) <= _MAX_RUNTIME_ERROR_BODY_LENGTH:
        return value
    return f"{value[:_MAX_RUNTIME_ERROR_BODY_LENGTH]}... [truncated]"


def _decode_runtime_error_body(raw: bytes) -> object:
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _truncate_runtime_error_body(text)


def _bounded_runtime_body(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        return _truncate_runtime_error_body(value)
    try:
        text = json.dumps(value, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return _truncate_runtime_error_body(str(value))
    if len(text) <= _MAX_RUNTIME_ERROR_BODY_LENGTH:
        return value
    return {"truncated": True, "preview": _truncate_runtime_error_body(text)}


def _runtime_failure_detail(
    operation: str,
    message: str,
    *,
    runtime_status: int | None = None,
    runtime_body: object | None = None,
) -> dict[str, object]:
    detail: dict[str, object] = {
        "message": f"Sandbox runtime {operation} failed",
        "error": _truncate_runtime_error_body(message),
    }
    if runtime_status is not None:
        detail["runtime_status"] = runtime_status
    if runtime_body is not None:
        detail["runtime_body"] = _bounded_runtime_body(runtime_body)
    return detail


def _invalid_runtime_response(
    operation: str,
    payload: object,
    exc: Exception,
) -> HTTPException:
    detail = _runtime_failure_detail(
        operation,
        f"runtime returned an invalid response: {exc}",
        runtime_body=payload,
    )
    logger.warning(
        "Sandbox runtime %s returned invalid response: %s; body=%r",
        operation,
        exc,
        detail.get("runtime_body"),
    )
    return HTTPException(status_code=502, detail=detail)


def request_runtime_json(
    req: request.Request,
    *,
    timeout: int | float,
    operation: str,
    transient_gateway: TransientGateway | None = None,
    expected_instance_id: str | None = None,
    expected_port: int | None = None,
) -> dict[str, object]:
    try:
        status_code, _, raw = request_endpoint_http(
            req,
            timeout=timeout,
            transient_gateway=transient_gateway,
            expected_instance_id=expected_instance_id,
            expected_port=expected_port,
        )
    except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        detail = _runtime_failure_detail(operation, str(exc))
        logger.warning(
            "Sandbox runtime %s transport failure for %s: %s",
            operation,
            req.full_url,
            exc,
        )
        raise HTTPException(status_code=502, detail=detail) from exc

    if status_code >= 400:
        runtime_body = _decode_runtime_error_body(raw)
        detail = _runtime_failure_detail(
            operation,
            f"runtime returned HTTP {status_code}",
            runtime_status=status_code,
            runtime_body=runtime_body,
        )
        logger.warning(
            "Sandbox runtime %s returned HTTP %s for %s; body=%r",
            operation,
            status_code,
            req.full_url,
            detail.get("runtime_body"),
        )
        raise HTTPException(status_code=502, detail=detail)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        runtime_body = _decode_runtime_error_body(raw)
        detail = _runtime_failure_detail(
            operation,
            "runtime returned malformed JSON",
            runtime_body=runtime_body,
        )
        logger.warning(
            "Sandbox runtime %s returned malformed JSON for %s: %s; body=%r",
            operation,
            req.full_url,
            exc,
            detail.get("runtime_body"),
        )
        raise HTTPException(status_code=502, detail=detail) from exc
    if not isinstance(payload, dict):
        detail = _runtime_failure_detail(
            operation,
            "runtime returned a non-object JSON response",
            runtime_body=payload,
        )
        logger.warning(
            "Sandbox runtime %s returned non-object JSON for %s; body=%r",
            operation,
            req.full_url,
            detail.get("runtime_body"),
        )
        raise HTTPException(status_code=502, detail=detail)
    return payload


class RuntimeProxy:
    def __init__(
        self,
        base_url: str,
        sandbox_id: str,
        *,
        headers: dict[str, str] | None = None,
        transient_gateway: TransientGateway | None = None,
        instance_id: str | None = None,
        port: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.sandbox_id = sandbox_id
        self.headers = dict(headers or {})
        self.transient_gateway = transient_gateway
        self.instance_id = instance_id
        self.port = port

    def _request_json(
        self,
        req: request.Request,
        *,
        timeout: int | float,
        operation: str,
    ) -> dict[str, object]:
        return request_runtime_json(
            req,
            timeout=timeout,
            operation=operation,
            transient_gateway=self.transient_gateway,
            expected_instance_id=self.instance_id,
            expected_port=self.port,
        )

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = dict(self.headers)
        if json_body:
            headers.update(
                {"Content-Type": "application/json", "Accept": "application/json"}
            )
        return headers

    async def execute_code(
        self,
        code: str,
        timeout_seconds: int,
        session_id: str | None = None,
    ) -> tuple[str, str, str | None, str | None, int | None]:
        def _execute() -> tuple[str, str, str | None, str | None, int | None]:
            path = (
                "/execute" if session_id is None else f"/sessions/{session_id}/execute"
            )
            body = json.dumps(
                {"code": code, "timeout_seconds": timeout_seconds}
            ).encode("utf-8")
            req = request.Request(
                f"{self.base_url}{path}",
                data=body,
                headers=self._headers(json_body=True),
                method="POST",
            )
            payload = self._request_json(
                req,
                # The runtime owns execution timeout and needs a small grace
                # window to terminate the session process group and return its
                # structured TimeoutError response.
                timeout=timeout_seconds + 5,
                operation="code execution request",
            )
            return (
                str(payload.get("stdout") or ""),
                str(payload.get("stderr") or ""),
                payload.get("result")
                if isinstance(payload.get("result"), str)
                else None,
                payload.get("error_name")
                if isinstance(payload.get("error_name"), str)
                else None,
                0 if payload.get("ok") else 1,
            )

        return await run_sync(_execute)

    async def create_session(
        self,
        session_id: str,
        request_obj: RuntimeSessionRequest,
    ) -> RuntimeSessionResponse:
        def _create() -> RuntimeSessionResponse:
            body = json.dumps(request_obj.model_dump()).encode("utf-8")
            req = request.Request(
                f"{self.base_url}/sessions/{session_id}",
                data=body,
                headers=self._headers(json_body=True),
                method="POST",
            )
            payload = self._request_json(
                req,
                timeout=120,
                operation="session create request",
            )
            try:
                return RuntimeSessionResponse(sandbox_id=self.sandbox_id, **payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "session create request", payload, exc
                ) from exc

        return await run_sync(_create)

    async def delete_session(self, session_id: str) -> bool:
        def _delete() -> bool:
            req = request.Request(
                f"{self.base_url}/sessions/{session_id}",
                headers=self._headers(),
                method="DELETE",
            )
            payload = self._request_json(
                req, timeout=30, operation="session delete request"
            )
            return bool(payload.get("deleted"))

        return await run_sync(_delete)

    async def execute_session_command(
        self,
        session_id: str,
        command: list[str],
        timeout_seconds: int,
        cwd: str | None,
    ) -> tuple[str, str, int | None]:
        def _execute() -> tuple[str, str, int | None]:
            body = json.dumps(
                {"command": command, "timeout_seconds": timeout_seconds, "cwd": cwd}
            ).encode("utf-8")
            req = request.Request(
                f"{self.base_url}/sessions/{session_id}/command",
                data=body,
                headers=self._headers(json_body=True),
                method="POST",
            )
            payload = self._request_json(
                req,
                timeout=timeout_seconds + 5,
                operation="command execution request",
            )
            exit_code = payload.get("exit_code")
            return (
                str(payload.get("stdout") or ""),
                str(payload.get("stderr") or ""),
                exit_code if isinstance(exit_code, int) else None,
            )

        return await run_sync(_execute)

    async def exec_session_process_command(
        self,
        session_id: str,
        request_obj: ExecCommandRequest,
    ) -> ExecCommandResponse:
        def _execute() -> ExecCommandResponse:
            body = json.dumps(request_obj.model_dump(exclude_none=True)).encode("utf-8")
            timeout = request_obj.timeout or 300
            if request_obj.yield_time_ms is not None:
                timeout = max(timeout, int(request_obj.yield_time_ms / 1000) + 30)
            req = request.Request(
                f"{self.base_url}/sessions/{session_id}/exec-command",
                data=body,
                headers=self._headers(json_body=True),
                method="POST",
            )
            payload = self._request_json(
                req,
                timeout=timeout + 5,
                operation="process command request",
            )
            try:
                return ExecCommandResponse(**payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "process command request", payload, exc
                ) from exc

        return await run_sync(_execute)

    async def write_session_process_stdin(
        self,
        session_id: str,
        request_obj: WriteStdinRequest,
    ) -> ExecCommandResponse:
        def _execute() -> ExecCommandResponse:
            body = json.dumps(request_obj.model_dump(exclude_none=True)).encode("utf-8")
            wait_ms = request_obj.yield_time_ms or 0
            req = request.Request(
                f"{self.base_url}/sessions/{session_id}/write-stdin",
                data=body,
                headers=self._headers(json_body=True),
                method="POST",
            )
            payload = self._request_json(
                req,
                timeout=int(wait_ms / 1000) + 35,
                operation="process stdin request",
            )
            try:
                return ExecCommandResponse(**payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "process stdin request", payload, exc
                ) from exc

        return await run_sync(_execute)

    async def terminate_session_process(
        self,
        session_id: str,
        process_id: str,
    ) -> ExecCommandResponse:
        def _delete() -> ExecCommandResponse:
            req = request.Request(
                f"{self.base_url}/sessions/{session_id}/processes/{process_id}",
                headers=self._headers(),
                method="DELETE",
            )
            payload = self._request_json(
                req, timeout=30, operation="process terminate request"
            )
            try:
                return ExecCommandResponse(**payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "process terminate request", payload, exc
                ) from exc

        return await run_sync(_delete)

    async def list_session_processes(self, session_id: str) -> ListProcessesResponse:
        def _list() -> ListProcessesResponse:
            req = request.Request(
                f"{self.base_url}/sessions/{session_id}/processes",
                headers=self._headers(),
                method="GET",
            )
            payload = self._request_json(
                req, timeout=30, operation="process list request"
            )
            try:
                return ListProcessesResponse(**payload)
            except ValidationError as exc:
                raise _invalid_runtime_response(
                    "process list request", payload, exc
                ) from exc

        return await run_sync(_list)
