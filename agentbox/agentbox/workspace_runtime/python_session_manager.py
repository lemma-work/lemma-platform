from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
from uuid import UUID

from agentbox.domain import (
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    PythonExecutionState,
    PythonResult,
)


@dataclass(slots=True)
class ManagedPythonSession:
    request: CreatePythonSessionRequest
    process: asyncio.subprocess.Process
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    results: dict[UUID, PythonResult] = field(default_factory=dict)

    def response_environment_keys(self) -> tuple[str, ...]:
        return self.request.environment_keys


class PythonSessionManager:
    def __init__(self, allowed_roots: tuple[str, ...]) -> None:
        self._roots = tuple(Path(root).resolve() for root in allowed_roots)
        self._sessions: dict[UUID, ManagedPythonSession] = {}
        self._lock = asyncio.Lock()

    async def create(
        self, request: CreatePythonSessionRequest
    ) -> tuple[ManagedPythonSession, bool]:
        cwd = Path(request.cwd).resolve(strict=True)
        if not cwd.is_dir() or not self._allowed(cwd):
            raise ValueError("Python session cwd is outside allowed roots")
        async with self._lock:
            existing = self._sessions.get(request.session_id)
            if existing is not None:
                if (
                    existing.request.cwd != request.cwd
                    or existing.response_environment_keys() != request.environment_keys
                ):
                    raise ValueError("Python session configuration conflicts")
                return existing, False
            session = ManagedPythonSession(
                request=request,
                process=await self._spawn(request),
            )
            self._sessions[request.session_id] = session
            return session, True

    async def get(self, session_id: UUID) -> ManagedPythonSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def count(self) -> int:
        async with self._lock:
            return len(self._sessions)

    async def execute(
        self, session_id: UUID, request: ExecutePythonRequest
    ) -> PythonResult:
        session = await self.get(session_id)
        if session is None:
            raise KeyError("Python session does not exist")
        async with session.lock:
            existing = session.results.get(request.operation_id)
            if existing is not None:
                return existing
            if session.process.returncode is not None:
                raise RuntimeError("Python session worker exited")
            assert session.process.stdin is not None
            assert session.process.stdout is not None
            payload = (
                json.dumps(
                    {
                        "operation_id": str(request.operation_id),
                        "code": request.code,
                        "environment": [
                            {"name": item.name, "value": item.value}
                            for item in request.environment
                        ],
                        "output_limit_bytes": request.output_limit_bytes,
                    },
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )
            session.process.stdin.write(payload)
            await session.process.stdin.drain()
            remaining = (
                request.deadline_at - datetime.now(timezone.utc)
            ).total_seconds()
            if remaining <= 0:
                await self._terminate(session.process)
                result = self._timeout_result(request.operation_id)
                session.process = await self._spawn(session.request)
                session.results[request.operation_id] = result
                return result
            try:
                line = await asyncio.wait_for(
                    session.process.stdout.readline(), timeout=remaining
                )
            except TimeoutError:
                await self._terminate(session.process)
                result = self._timeout_result(request.operation_id)
                session.process = await self._spawn(session.request)
                session.results[request.operation_id] = result
                return result
            if not line:
                raise RuntimeError("Python session worker closed its protocol")
            body = json.loads(line)
            result = PythonResult(
                operation_id=UUID(body["operation_id"]),
                state=PythonExecutionState(body["state"]),
                stdout=str(body["stdout"]),
                stderr=str(body["stderr"]),
                result=body.get("result"),
                error_name=body.get("error_name"),
                error_message=body.get("error_message"),
                traceback=body.get("traceback"),
                output_truncated=bool(body["output_truncated"]),
            )
            if result.operation_id != request.operation_id:
                raise RuntimeError("Python worker returned a mismatched operation ID")
            session.results[request.operation_id] = result
            return result

    async def restart(self, session_id: UUID) -> ManagedPythonSession:
        session = await self.get(session_id)
        if session is None:
            raise KeyError("Python session does not exist")
        async with session.lock:
            await self._terminate(session.process)
            session.process = await self._spawn(session.request)
            session.results.clear()
        return session

    async def delete(self, session_id: UUID) -> bool:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        async with session.lock:
            await self._terminate(session.process)
        return True

    async def quiesce(self) -> int:
        async with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            async with session.lock:
                await self._terminate(session.process)
        return len(sessions)

    async def _spawn(
        self, request: CreatePythonSessionRequest
    ) -> asyncio.subprocess.Process:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"AGENTBOX_RUNTIME_TOKEN", "AGENTBOX_RUNTIME_TOKEN_FILE"}
        }
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agentbox.workspace_runtime.python_worker",
            cwd=request.cwd,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    def _allowed(self, path: Path) -> bool:
        return any(path == root or path.is_relative_to(root) for root in self._roots)

    @staticmethod
    def _timeout_result(operation_id: UUID) -> PythonResult:
        return PythonResult(
            operation_id=operation_id,
            state=PythonExecutionState.TIMED_OUT,
            stdout="",
            stderr="",
            result=None,
            error_name="TimeoutError",
            error_message="Python execution deadline elapsed",
            traceback=None,
            output_truncated=False,
        )
