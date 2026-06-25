"""In-process fake AgentBox manager for fast e2e (no Docker).

Implements the same HTTP contract the real ``AgentBoxClient`` calls, but each
"sandbox" is just a temp directory and ``exec_command`` / ``execute_python`` run
the command in a local subprocess inside it. This lets workspace/CLI agent tools
run for real in CI without a Docker workspace image — the whole
tool → AgentBoxClient → manager path is exercised; only the isolation boundary
is a temp dir instead of a container.

Used when ``settings.e2e_sandbox_mode == "fake"`` (see the e2e fixtures, which
run ``create_fake_agentbox_app()`` on the pinned AgentBox port and point
``AGENTBOX_API_URL`` at it). Not used in production.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI

from agentbox_client.generated.manager.models import (
    DeleteResponse,
    ExecCommandRequest,
    ExecCommandResponse,
    ExecutePythonRequest,
    ExecutePythonResponse,
    ListProcessesResponse,
    RuntimeSessionHeartbeatResponse,
    RuntimeSessionRequest,
    RuntimeSessionResponse,
    SandboxEnsureRequest,
    SandboxResponse,
    SandboxSummary,
)

_DEFAULT_TIMEOUT = 300


@dataclass
class _Session:
    cwd: str
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class _Sandbox:
    root: Path
    env: dict[str, str] = field(default_factory=dict)
    sessions: dict[str, _Session] = field(default_factory=dict)


class FakeAgentBoxState:
    """Holds sandbox temp dirs + sessions; maps sandbox-absolute paths into them."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.sandboxes: dict[str, _Sandbox] = {}

    def ensure_sandbox(self, sandbox_id: str, env: dict[str, str]) -> _Sandbox:
        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox is None:
            root = self.base_dir / sandbox_id
            root.mkdir(parents=True, exist_ok=True)
            sandbox = _Sandbox(root=root, env=dict(env))
            self.sandboxes[sandbox_id] = sandbox
        else:
            sandbox.env.update(env)
        return sandbox

    def resolve(self, sandbox: _Sandbox, cwd: str) -> Path:
        """Map a sandbox-absolute path (e.g. /workspace/conversations/X) into the
        sandbox temp dir, creating it. Treats the sandbox root as ``/``."""
        rel = cwd.lstrip("/") if cwd else ""
        path = (sandbox.root / rel).resolve()
        # Keep everything under the sandbox root.
        if not str(path).startswith(str(sandbox.root.resolve())):
            path = sandbox.root
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup(self) -> None:
        shutil.rmtree(self.base_dir, ignore_errors=True)


def create_fake_agentbox_app(*, base_dir: Path | None = None) -> FastAPI:
    """Build a FastAPI app implementing the AgentBox manager HTTP contract."""
    state = FakeAgentBoxState(base_dir or Path(tempfile.mkdtemp(prefix="fake-agentbox-")))
    app = FastAPI(title="fake-agentbox")
    app.state.fake = state

    def _summary(sandbox_id: str) -> SandboxSummary:
        return SandboxSummary(id=sandbox_id, ready=True, status="RUNNING")

    @app.put("/sandboxes/{sandbox_id}")
    async def ensure_sandbox(sandbox_id: str, body: SandboxEnsureRequest) -> SandboxResponse:
        state.ensure_sandbox(sandbox_id, body.env or {})
        return SandboxResponse(sandbox=_summary(sandbox_id))

    @app.get("/sandboxes/{sandbox_id}")
    async def get_sandbox(sandbox_id: str):
        if sandbox_id not in state.sandboxes:
            from fastapi import Response

            return Response(status_code=404)
        return _summary(sandbox_id)

    @app.delete("/sandboxes/{sandbox_id}")
    async def delete_sandbox(sandbox_id: str) -> DeleteResponse:
        sandbox = state.sandboxes.pop(sandbox_id, None)
        if sandbox is not None:
            shutil.rmtree(sandbox.root, ignore_errors=True)
        return DeleteResponse(sandbox_id=sandbox_id, deleted=sandbox is not None)

    @app.post("/sandboxes/{sandbox_id}/heartbeat")
    async def heartbeat_sandbox(sandbox_id: str) -> dict[str, bool]:
        return {"active": sandbox_id in state.sandboxes}

    @app.put("/sandboxes/{sandbox_id}/sessions/{session_id}")
    async def create_session(
        sandbox_id: str, session_id: str, body: RuntimeSessionRequest
    ) -> RuntimeSessionResponse:
        sandbox = state.ensure_sandbox(sandbox_id, {})
        sandbox.sessions[session_id] = _Session(cwd=body.cwd or "/workspace", env=dict(body.env or {}))
        return RuntimeSessionResponse(
            sandbox_id=sandbox_id,
            session_id=session_id,
            cwd=body.cwd or "/workspace",
            env_keys=sorted((body.env or {}).keys()),
        )

    @app.delete("/sandboxes/{sandbox_id}/sessions/{session_id}")
    async def delete_session(sandbox_id: str, session_id: str) -> dict[str, bool]:
        sandbox = state.sandboxes.get(sandbox_id)
        existed = bool(sandbox and sandbox.sessions.pop(session_id, None))
        return {"deleted": existed}

    @app.post("/sandboxes/{sandbox_id}/sessions/{session_id}/heartbeat")
    async def heartbeat_session(
        sandbox_id: str, session_id: str
    ) -> RuntimeSessionHeartbeatResponse:
        sandbox = state.sandboxes.get(sandbox_id)
        active = bool(sandbox and session_id in sandbox.sessions)
        return RuntimeSessionHeartbeatResponse(
            sandbox_id=sandbox_id, session_id=session_id, active=active
        )

    def _session(sandbox_id: str, session_id: str) -> tuple[_Sandbox, _Session]:
        sandbox = state.ensure_sandbox(sandbox_id, {})
        session = sandbox.sessions.get(session_id)
        if session is None:
            session = _Session(cwd="/workspace")
            sandbox.sessions[session_id] = session
        return sandbox, session

    async def _run(
        sandbox: _Sandbox, session: _Session, argv: list[str], *, cwd: str, timeout: int
    ) -> tuple[int | None, str, str, bool]:
        # Run via blocking subprocess.run in a worker thread (not asyncio
        # subprocess): the fake server runs in its own thread/loop, where asyncio
        # child-watchers are unreliable, whereas subprocess.run works anywhere.
        workdir = state.resolve(sandbox, cwd)
        env = {**os.environ, **sandbox.env, **session.env}

        def _call() -> tuple[int | None, str, str, bool]:
            try:
                completed = subprocess.run(
                    argv,
                    cwd=str(workdir),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return completed.returncode, completed.stdout, completed.stderr, True
            except subprocess.TimeoutExpired as exc:
                out = exc.stdout or ""
                err = (exc.stderr or "") + f"\ntimed out after {timeout}s"
                if isinstance(out, bytes):
                    out = out.decode(errors="replace")
                if isinstance(err, bytes):
                    err = err.decode(errors="replace")
                return None, out, err, False

        return await asyncio.to_thread(_call)

    @app.post("/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command")
    async def exec_command(
        sandbox_id: str, session_id: str, body: ExecCommandRequest
    ) -> ExecCommandResponse:
        sandbox, session = _session(sandbox_id, session_id)
        cwd = body.workdir or session.cwd
        code, out, err, completed = await _run(
            sandbox,
            session,
            ["/bin/sh", "-c", body.cmd],
            cwd=cwd,
            timeout=body.timeout or _DEFAULT_TIMEOUT,
        )
        return ExecCommandResponse(
            success=code == 0,
            stdout=out,
            stderr=err,
            exit_code=code,
            completed=completed,
            error=None if completed else "timeout",
        )

    @app.post("/sandboxes/{sandbox_id}/sessions/{session_id}/python")
    async def execute_python(
        sandbox_id: str, session_id: str, body: ExecutePythonRequest
    ) -> ExecutePythonResponse:
        sandbox, session = _session(sandbox_id, session_id)
        code, out, err, completed = await _run(
            sandbox,
            session,
            [sys.executable, "-c", body.code],
            cwd=session.cwd,
            timeout=body.timeout_seconds or 60,
        )
        ok = completed and code == 0
        return ExecutePythonResponse(
            sandbox_id=sandbox_id,
            session_id=session_id,
            stdout=out,
            stderr=err if completed else (err or "timeout"),
            result=None,
            error_name=None if ok else "Error",
            exit_code=code,
            status="completed" if ok else "error",
        )

    @app.post("/sandboxes/{sandbox_id}/sessions/{session_id}/stdin")
    async def write_stdin(
        sandbox_id: str, session_id: str
    ) -> ExecCommandResponse:
        # Long-running/interactive processes aren't modelled by the fake; report
        # nothing pending so callers don't block.
        return ExecCommandResponse(success=True, stdout="", stderr="", completed=True)

    @app.delete("/sandboxes/{sandbox_id}/sessions/{session_id}/processes/{process_id}")
    async def terminate_process(
        sandbox_id: str, session_id: str, process_id: str
    ) -> ExecCommandResponse:
        return ExecCommandResponse(success=True, stdout="", stderr="", completed=True)

    @app.get("/sandboxes/{sandbox_id}/sessions/{session_id}/processes")
    async def list_processes(sandbox_id: str, session_id: str) -> ListProcessesResponse:
        return ListProcessesResponse(processes=[])

    @app.post("/sandboxes/{sandbox_id}/apps/{app_name}/access")
    async def app_access(sandbox_id: str, app_name: str) -> dict[str, str]:
        return {"url": f"http://fake-agentbox.local/{sandbox_id}/{app_name}", "token": uuid.uuid4().hex}

    return app
