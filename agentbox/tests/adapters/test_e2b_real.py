from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

from e2b_code_interpreter import AsyncSandbox
from fastapi import FastAPI
import httpx
import pytest

from agentbox.adapters.e2b import E2BAdapterConfig, E2BSandboxAdapter
from agentbox.api.port_proxy import access_router, create_port_proxy_http_client
from agentbox.domain import (
    AdmissionClass,
    ByteRange,
    CreatePythonSessionRequest,
    EnvironmentVariable,
    ExecutePythonRequest,
    PortProtocol,
    ProcessState,
    PythonExecutionState,
    PythonSessionState,
    SandboxCapability,
    SandboxKey,
    SandboxProfileRef,
    StartProcessRequest,
    TerminalSize,
    WorkloadKind,
)
from agentbox.filesystem import FilesystemService
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.port_access import PortAccessService, PortAccessSigner
from agentbox.ports import ProviderAllocationRef
from agentbox.processes import ProcessExecutionService
from agentbox.profiles import E2BProfileArtifact, ProfileRegistry, SandboxProfile
from agentbox.python_sessions import PythonSessionService


_REQUIRED_ENV = (
    "E2B_API_KEY",
    "AGENTBOX_E2B_WORKSPACE_TEMPLATE",
    "AGENTBOX_E2B_WORKSPACE_BUILD_ID",
    "AGENTBOX_E2B_FUNCTION_TEMPLATE",
    "AGENTBOX_E2B_FUNCTION_BUILD_ID",
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("AGENTBOX_RUN_E2B_TESTS") != "1"
        or any(not os.getenv(name) for name in _REQUIRED_ENV),
        reason=(
            "set AGENTBOX_RUN_E2B_TESTS=1 and the immutable E2B template/build "
            "environment variables for real E2B conformance"
        ),
    ),
]


def _workspace_profile() -> SandboxProfile:
    return SandboxProfile(
        ref=SandboxProfileRef(name="workspace-python-v1", digest=f"sha256:{'e' * 64}"),
        workload_kind=WorkloadKind.WORKSPACE,
        runtime_abi="lemma-workspace-python-1-node-24",
        capabilities=frozenset(
            {
                SandboxCapability.PROCESS,
                SandboxCapability.PTY,
                SandboxCapability.PYTHON_SESSION,
                SandboxCapability.FILESYSTEM,
                SandboxCapability.PORT_ACCESS,
                SandboxCapability.BROWSER,
            }
        ),
        allowed_roots=("/workspace",),
        docker=None,
        e2b=E2BProfileArtifact(
            template_id=os.environ["AGENTBOX_E2B_WORKSPACE_TEMPLATE"],
            build_id=os.environ["AGENTBOX_E2B_WORKSPACE_BUILD_ID"],
        ),
    )


def _function_profile() -> SandboxProfile:
    return SandboxProfile(
        ref=SandboxProfileRef(name="function-python-v1", digest=f"sha256:{'f' * 64}"),
        workload_kind=WorkloadKind.FUNCTION,
        runtime_abi="lemma-function-python-3.14-linux-x86_64-1",
        capabilities=frozenset({SandboxCapability.PORT_ACCESS}),
        allowed_roots=("/tmp",),
        docker=None,
        e2b=E2BProfileArtifact(
            template_id=os.environ["AGENTBOX_E2B_FUNCTION_TEMPLATE"],
            build_id=os.environ["AGENTBOX_E2B_FUNCTION_BUILD_ID"],
        ),
    )


def _adapter(registry: ProfileRegistry, *, scope: str) -> E2BSandboxAdapter:
    return E2BSandboxAdapter(
        registry,
        E2BAdapterConfig(
            api_key=os.environ["E2B_API_KEY"],
            scope=scope,
            request_timeout_seconds=60,
            workspace_timeout_seconds=600,
            function_timeout_seconds=300,
        ),
    )


async def _provider_id(database: StateDatabase, key: SandboxKey) -> str | None:
    async with database.uow() as uow:
        allocation = await uow.repository.latest_allocation(key)
        await uow.commit()
    return allocation.provider_id if allocation is not None else None


async def _read_terminal(
    processes: ProcessExecutionService,
    key: SandboxKey,
    operation_id,
    *,
    deadline_at: datetime,
    wait_seconds: float = 10,
):
    chunks = []
    after_sequence = 0
    snapshot = None
    while datetime.now(timezone.utc) < deadline_at:
        snapshot = await processes.read_output(
            key,
            operation_id,
            after_sequence=after_sequence,
            wait_seconds=wait_seconds,
            deadline_at=deadline_at,
        )
        chunks.extend(snapshot.chunks)
        after_sequence = snapshot.next_sequence - 1
        if snapshot.state != ProcessState.RUNNING:
            return snapshot, b"".join(chunk.data for chunk in chunks)
    raise AssertionError(f"process {operation_id} did not reach a terminal state")


async def _kill_exact(provider_id: str | None) -> None:
    if provider_id is None:
        return
    try:
        await AsyncSandbox.kill(
            provider_id,
            api_key=os.environ["E2B_API_KEY"],
            request_timeout=60,
        )
    except Exception:
        # The lifecycle may already have destroyed this exact sandbox. Cleanup is
        # deliberately exact-ID only; this suite never performs broad account kills.
        pass


async def test_real_e2b_workspace_full_conformance(tmp_path: Path) -> None:
    profile = _workspace_profile()
    registry = ProfileRegistry((profile,))
    adapter = _adapter(registry, scope=f"e2b:workspace-test:{uuid4()}")
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(database, adapter)
    processes = ProcessExecutionService(database, adapter)
    filesystem = FilesystemService(database, adapter)
    python_sessions = PythonSessionService(database, adapter)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(minutes=8)
    provider_id: str | None = None

    try:
        handle = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
        provider_id = await _provider_id(database, key)
        assert handle.ready is True
        assert provider_id is not None
        assert database.active_units_of_work == 0

        versions_id = uuid4()
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=versions_id,
                shell_command=(
                    "printf 'node:%s\\n' \"$(node --version)\"; "
                    "printf 'pnpm:%s\\n' \"$(pnpm --version)\"; "
                    "printf 'uv:%s\\n' \"$(uv --version)\"; "
                    "printf 'python:%s\\n' "
                    '"$(python -c \'import sys; '
                    'print("%d.%d" % sys.version_info[:2])\')"; '
                    "printf 'lemma:%s\\n' \"$(lemma --version)\"; "
                    "lit --help >/dev/null"
                ),
                argv=None,
                cwd="/workspace",
                environment=(),
                tty=None,
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        versions, versions_output = await _read_terminal(
            processes, key, versions_id, deadline_at=deadline
        )
        assert versions.state == ProcessState.SUCCEEDED, versions_output
        assert b"node:v24.18.0" in versions_output
        assert b"pnpm:11.15.1" in versions_output
        assert b"uv:uv 0.11.31" in versions_output
        assert b"python:3.14" in versions_output
        assert b"lemma:lemma " in versions_output

        shell_id = uuid4()
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=shell_id,
                shell_command=(
                    'read value; printf \'stdout:%s:%s\\n\' "$value" "$TOKEN"; '
                    "printf 'stderr-ok\\n' >&2"
                ),
                argv=None,
                cwd="/workspace",
                environment=(EnvironmentVariable("TOKEN", "ephemeral"),),
                tty=None,
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        await processes.send_input(key, shell_id, b"hello\n", deadline_at=deadline)
        shell_result, shell_output = await _read_terminal(
            processes, key, shell_id, deadline_at=deadline
        )
        assert shell_result.state == ProcessState.SUCCEEDED, shell_output
        assert b"stdout:hello:ephemeral" in shell_output
        assert b"stderr-ok" in shell_output

        pty_id = uuid4()
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=pty_id,
                shell_command=(
                    "read value; size=$(stty size); "
                    'printf \'pty:%s:%s\\n\' "$value" "$size"'
                ),
                argv=None,
                cwd="/workspace",
                environment=(),
                tty=TerminalSize(cols=80, rows=24),
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        await processes.resize(
            key,
            pty_id,
            TerminalSize(cols=100, rows=40),
            deadline_at=deadline,
        )
        await processes.send_input(key, pty_id, b"hello-pty\n", deadline_at=deadline)
        pty_result, pty_output = await _read_terminal(
            processes, key, pty_id, deadline_at=deadline
        )
        assert pty_result.state == ProcessState.SUCCEEDED, pty_output
        assert b"pty:hello-pty:40 100" in pty_output

        binary_payload = b"\x00agentbox-e2b\xff" * 64 * 1024
        written = await filesystem.write(
            key,
            "/workspace/binary.dat",
            binary_payload,
            expected_sha256=None,
            deadline_at=deadline,
        )
        ranged = await filesystem.read(
            key,
            "/workspace/binary.dat",
            ByteRange(offset=31, length=8193),
            deadline_at=deadline,
        )
        listed = await filesystem.list(key, "/workspace", deadline_at=deadline)
        await filesystem.move(
            key,
            "/workspace/binary.dat",
            "/workspace/moved.dat",
            deadline_at=deadline,
        )
        assert written.size_bytes == len(binary_payload)
        assert ranged == binary_payload[31 : 31 + 8193]
        assert any(item.path == "/workspace/binary.dat" for item in listed)
        assert await filesystem.delete(
            key, "/workspace/moved.dat", recursive=False, deadline_at=deadline
        )

        session_id = uuid4()
        session_request = CreatePythonSessionRequest(
            session_id=session_id,
            cwd="/workspace",
            environment_keys=("PYTHON_MARK",),
            deadline_at=deadline,
        )
        session, session_created = await python_sessions.create(key, session_request)
        first_python, _ = await python_sessions.execute(
            key,
            session_id,
            ExecutePythonRequest(
                operation_id=uuid4(),
                code="value = 40",
                environment=(),
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        second_python, _ = await python_sessions.execute(
            key,
            session_id,
            ExecutePythonRequest(
                operation_id=uuid4(),
                code=(
                    "import os, sys\n"
                    "print('native-python')\n"
                    "(value + 2, os.environ['PYTHON_MARK'], "
                    "sys.version_info[:2])"
                ),
                environment=(EnvironmentVariable("PYTHON_MARK", "e2b"),),
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        assert session_created is True
        assert session.state == PythonSessionState.ACTIVE
        assert first_python.state == PythonExecutionState.SUCCEEDED
        assert second_python.state == PythonExecutionState.SUCCEEDED
        assert second_python.stdout == "native-python\n"
        assert second_python.result == "(42, 'e2b', (3, 14))"

        await filesystem.write(
            key,
            "/workspace/browser.html",
            b"<!doctype html><title>E2B browser</title>"
            b"<button id='probe'>e2b-browser-ready</button>",
            expected_sha256=None,
            deadline_at=deadline,
        )
        browser_start_id = uuid4()
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=browser_start_id,
                shell_command=None,
                argv=("start-browser", "file:///workspace/browser.html"),
                cwd="/workspace",
                environment=(),
                tty=None,
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        browser_result, browser_output = await _read_terminal(
            processes, key, browser_start_id, deadline_at=deadline, wait_seconds=20
        )
        assert browser_result.state == ProcessState.SUCCEEDED, browser_output

        snapshot_id = uuid4()
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=snapshot_id,
                shell_command=None,
                argv=("agent-browser", "snapshot", "-i", "-u"),
                cwd="/workspace",
                environment=(),
                tty=None,
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        snapshot, snapshot_output = await _read_terminal(
            processes, key, snapshot_id, deadline_at=deadline, wait_seconds=20
        )
        assert snapshot.state == ProcessState.SUCCEEDED, snapshot_output
        assert b"e2b-browser-ready" in snapshot_output

        await filesystem.write(
            key,
            "/workspace/release-persistence",
            b"survives",
            expected_sha256=None,
            deadline_at=deadline,
        )
        released = await lifecycle.release(key, deadline_at=deadline)
        assert released.ready is False
        assert database.active_units_of_work == 0

        resumed = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
        resumed_provider_id = await _provider_id(database, key)
        persisted = await filesystem.read(
            key,
            "/workspace/release-persistence",
            ByteRange(offset=0, length=None),
            deadline_at=deadline,
        )
        stale_session = await python_sessions.inspect(key, session_id)
        assert resumed.ready is True
        assert resumed_provider_id == provider_id
        assert resumed.allocation_id == handle.allocation_id
        assert resumed.allocation_epoch == handle.allocation_epoch + 1
        assert persisted == b"survives"
        assert stale_session.state == PythonSessionState.STALE
        assert database.active_units_of_work == 0

        assert await lifecycle.destroy(key, deadline_at=deadline)
    finally:
        provider_id = provider_id or await _provider_id(database, key)
        await _kill_exact(provider_id)
        await adapter.close()
        await database.dispose()


async def test_real_e2b_function_runtime_port_and_exact_destroy(
    tmp_path: Path,
) -> None:
    profile = _function_profile()
    registry = ProfileRegistry((profile,))
    adapter = _adapter(registry, scope=f"e2b:function-test:{uuid4()}")
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(database, adapter)
    port_access = PortAccessService(
        database,
        adapter,
        PortAccessSigner(b"e2b-function-conformance-key-0001"),
        public_base_url="http://agentbox.test",
    )
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    provider_id: str | None = None

    try:
        handle = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.LATENCY,
            deadline_at=deadline,
        )
        assert handle.ready is True
        async with database.uow() as uow:
            allocation = await uow.repository.current_allocation(key)
        assert allocation is not None
        assert allocation.provider_id is not None
        provider_id = allocation.provider_id

        target = await adapter.resolve_port_target(
            ProviderAllocationRef(
                provider_id=allocation.provider_id,
                provider_instance_id=allocation.provider_instance_id,
                allocation_id=allocation.allocation_id,
                allocation_token=allocation.allocation_token,
                key=key,
            ),
            port=8090,
            protocol=PortProtocol.HTTP,
            deadline_at=deadline,
        )
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{target.base_url}/healthz",
                headers={header.name: header.value for header in target.headers},
            )
        assert response.status_code == 200
        assert response.json() == {
            "ready": True,
            "runtime_abi": "lemma-function-python-3.14-linux-x86_64-1",
        }

        grant = await port_access.create(
            key,
            port=8090,
            protocol=PortProtocol.HTTP,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        )
        token = grant.url.split("/port-access/", 1)[1].rstrip("/")
        proxy_app = FastAPI()
        proxy_app.state.port_access = port_access
        proxy_app.state.port_proxy_http_client = create_port_proxy_http_client()
        proxy_app.include_router(access_router)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=proxy_app),
                base_url="http://agentbox.test",
            ) as proxy_client:
                responses = await asyncio.gather(
                    *(
                        proxy_client.get(f"/port-access/{token}/healthz")
                        for _ in range(10)
                    )
                )
        finally:
            await proxy_app.state.port_proxy_http_client.aclose()
        assert all(item.status_code == 200 for item in responses)
        assert all(item.json()["ready"] is True for item in responses)
        assert database.active_units_of_work == 0
        assert await lifecycle.destroy(key, deadline_at=deadline)
    finally:
        provider_id = provider_id or await _provider_id(database, key)
        await _kill_exact(provider_id)
        await adapter.close()
        await database.dispose()
