from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

from e2b_code_interpreter import AsyncSandbox
import httpx
import pytest

from agentbox.adapters.e2b import E2BAdapterConfig, E2BSandboxAdapter
from agentbox.domain import (
    AdmissionClass,
    AgentBoxError,
    AllocationState,
    ByteRange,
    CreatePythonSessionRequest,
    EnvironmentVariable,
    ExecutePythonRequest,
    ProcessState,
    PythonExecutionState,
    PythonSessionState,
    RetryDisposition,
    SandboxCapability,
    SandboxKey,
    SandboxProfileRef,
    StartProcessRequest,
    TerminalSize,
    WorkloadKind,
)
from agentbox.filesystem import FilesystemService
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.maintenance import SandboxMaintenanceWorker
from agentbox.persistence.uow import StateDatabase
from agentbox.port_access import PortAccessService, PortAccessSigner
from agentbox.processes import ProcessExecutionService
from agentbox.profiles import E2BProfileArtifact, ProfileRegistry, SandboxProfile
from agentbox.python_sessions import PythonSessionService
from tests.adapters.workspace_python_contract import python_install_probe_command


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


def _adapter(
    registry: ProfileRegistry,
    *,
    scope: str,
    workspace_timeout_seconds: int = 600,
    workspace_timeout_refresh_seconds: int = 60,
) -> E2BSandboxAdapter:
    return E2BSandboxAdapter(
        registry,
        E2BAdapterConfig(
            api_key=os.environ["E2B_API_KEY"],
            scope=scope,
            request_timeout_seconds=60,
            workspace_timeout_seconds=workspace_timeout_seconds,
            workspace_timeout_refresh_seconds=workspace_timeout_refresh_seconds,
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
                    "\"$(python -c 'import sys; "
                    'print("%d.%d" % sys.version_info[:2])\')"; '
                    "printf 'python3:%s\\n' "
                    "\"$(python3 -c 'import sys; "
                    'print("%d.%d" % sys.version_info[:2])\')"; '
                    "printf 'python-path:%s\\n' \"$(command -v python)\"; "
                    "printf 'python3-path:%s\\n' \"$(command -v python3)\"; "
                    "printf 'pip:%s\\n' \"$(pip --version)\"; "
                    "printf 'pip3:%s\\n' \"$(pip3 --version)\"; "
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
        assert b"python3:3.14" in versions_output
        assert b"python-path:" in versions_output
        assert b"python3-path:" in versions_output
        assert b"pip 26.1.2 from /opt/agentbox-python/" in versions_output
        assert versions_output.count(b"(python 3.14)") == 2
        assert b"lemma:lemma " in versions_output

        install_id = uuid4()
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=install_id,
                shell_command=python_install_probe_command(),
                argv=None,
                cwd="/workspace",
                environment=(),
                tty=None,
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        installed, install_output = await _read_terminal(
            processes, key, install_id, deadline_at=deadline
        )
        assert installed.state == ProcessState.SUCCEEDED, install_output
        assert b"shared-3.14" in install_output

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
                    "import agentbox_install_probe, os, sys\n"
                    "print('native-python')\n"
                    "(value + 2, os.environ['PYTHON_MARK'], "
                    "sys.version_info[:2], agentbox_install_probe.VALUE)"
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
        assert second_python.result == "(42, 'e2b', (3, 14), 'shared-3.14')"

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


async def test_real_e2b_missing_workspace_is_fenced_and_recreated(
    tmp_path: Path,
) -> None:
    profile = _workspace_profile()
    adapter = _adapter(
        ProfileRegistry((profile,)),
        scope=f"e2b:workspace-recovery-test:{uuid4()}",
    )
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(database, adapter)
    filesystem = FilesystemService(database, adapter)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(minutes=6)
    original_provider_id: str | None = None
    recovered_provider_id: str | None = None

    try:
        created = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
        original_provider_id = await _provider_id(database, key)
        assert created.ready is True
        assert original_provider_id is not None
        await filesystem.write(
            key,
            "/workspace/will-be-discarded",
            b"old-generation",
            expected_sha256=None,
            deadline_at=deadline,
        )

        await _kill_exact(original_provider_id)
        with pytest.raises(AgentBoxError) as raised:
            await lifecycle.ensure(
                key,
                profile.ref,
                admission_class=AdmissionClass.INTERACTIVE,
                deadline_at=deadline,
                verify_ready=True,
            )
        assert raised.value.retry == RetryDisposition.SAFE_SAME_OPERATION

        recovered = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
            verify_ready=True,
        )
        recovered_provider_id = await _provider_id(database, key)
        assert recovered.ready is True
        assert recovered.allocation_id != created.allocation_id
        assert recovered_provider_id is not None
        assert recovered_provider_id != original_provider_id
        await filesystem.write(
            key,
            "/workspace/new-generation",
            b"fresh",
            expected_sha256=None,
            deadline_at=deadline,
        )
        assert (
            await filesystem.read(
                key,
                "/workspace/new-generation",
                ByteRange(offset=0, length=None),
                deadline_at=deadline,
            )
            == b"fresh"
        )
        assert await lifecycle.destroy(key, deadline_at=deadline)
    finally:
        await _kill_exact(original_provider_id)
        await _kill_exact(recovered_provider_id)
        await adapter.close()
        await database.dispose()


async def test_real_e2b_expiry_recreates_but_profile_change_preserves_disk(
    tmp_path: Path,
) -> None:
    profile = _workspace_profile()
    replacement_profile = replace(
        profile,
        ref=SandboxProfileRef(
            name="workspace-python-v2",
            digest=f"sha256:{'d' * 64}",
        ),
    )
    adapter = _adapter(
        ProfileRegistry((profile, replacement_profile)),
        scope=f"e2b:workspace-expiry-test:{uuid4()}",
    )
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(
        database,
        adapter,
        workspace_retention_seconds=0,
    )
    maintenance = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
        batch_size=1,
    )
    filesystem = FilesystemService(database, adapter)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(minutes=6)
    original_provider_id: str | None = None
    fresh_provider_id: str | None = None
    replacement_provider_id: str | None = None

    try:
        await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
        original_provider_id = await _provider_id(database, key)
        assert original_provider_id is not None

        assert await maintenance.run_once(deadline_at=deadline) == 1
        assert await maintenance.run_once(deadline_at=deadline) == 1

        fresh = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
            verify_ready=True,
        )
        fresh_provider_id = await _provider_id(database, key)
        assert fresh.ready is True
        assert fresh_provider_id is not None
        assert fresh_provider_id != original_provider_id

        # Shipping a new workspace profile must never cost the user their
        # files. E2B workspace storage is the sandbox itself, so replacing the
        # allocation to adopt a new digest would delete everything the agent
        # has written. The workspace keeps running its existing profile until
        # it is recreated from scratch.
        await filesystem.write(
            key,
            "/workspace/survives-profile-change",
            b"kept",
            expected_sha256=None,
            deadline_at=deadline,
        )
        replacement = await lifecycle.ensure(
            key,
            replacement_profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
            verify_ready=True,
        )
        replacement_provider_id = await _provider_id(database, key)
        assert replacement.ready is True
        assert replacement_provider_id == fresh_provider_id
        assert (
            await filesystem.read(
                key,
                "/workspace/survives-profile-change",
                ByteRange(offset=0, length=None),
                deadline_at=deadline,
            )
            == b"kept"
        )
        assert await lifecycle.destroy(key, deadline_at=deadline)
    finally:
        await _kill_exact(original_provider_id)
        await _kill_exact(fresh_provider_id)
        await _kill_exact(replacement_provider_id)
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

        lease = await port_access.lease_function_runtime(
            key.logical_id,
            deadline_at=deadline,
            required_valid_until=datetime.now(timezone.utc) + timedelta(minutes=2),
        )
        async with httpx.AsyncClient(timeout=20) as client:
            responses = await asyncio.gather(
                *(
                    client.get(
                        f"{lease.url}healthz",
                        headers={
                            header.name: header.value
                            for header in lease.request_headers
                        },
                    )
                    for _ in range(10)
                )
            )
        assert all(item.status_code == 200 for item in responses)
        assert all(
            item.json()
            == {
                "ready": True,
                "runtime_abi": "lemma-function-python-3.14-linux-x86_64-1",
                "protocol_version": 2,
            }
            for item in responses
        )
        assert lease.allocation_id == allocation.allocation_id
        assert lease.allocation_epoch == allocation.allocation_epoch
        assert lease.profile == profile.ref
        assert database.active_units_of_work == 0
        assert await lifecycle.destroy(key, deadline_at=deadline)
    finally:
        provider_id = provider_id or await _provider_id(database, key)
        await _kill_exact(provider_id)
        await adapter.close()
        await database.dispose()


async def test_real_e2b_active_workspace_outlives_the_provider_timeout(
    tmp_path: Path,
) -> None:
    """An actively-used workspace must never be paused by E2B mid-session.

    E2B's `timeout` is a continuous-runtime ceiling, not an idle timer, so
    without a refresh on activity the provider stops a busy workspace once the
    window elapses. Compressed to E2B's 60s minimum so the behaviour is
    observable in a test.

    A background process is the proof: a workspace pause is filesystem-only and
    cold-boots, so a process that is still running after more than one full
    timeout window can only mean the sandbox was never paused.
    """

    profile = _workspace_profile()
    adapter = _adapter(
        ProfileRegistry((profile,)),
        scope=f"e2b:workspace-timeout-refresh:{uuid4()}",
        workspace_timeout_seconds=60,
        workspace_timeout_refresh_seconds=5,
    )
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(database, adapter)
    processes = ProcessExecutionService(database, adapter)
    filesystem = FilesystemService(database, adapter)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(minutes=6)
    provider_id: str | None = None

    try:
        handle = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
            verify_ready=True,
        )
        assert handle.ready is True
        provider_id = await _provider_id(database, key)

        marker = uuid4()
        background_id = uuid4()
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=background_id,
                shell_command=f"sleep 300 # {marker}",
                argv=None,
                cwd="/workspace",
                environment=(),
                tty=None,
                output_limit_bytes=4096,
                deadline_at=deadline,
            ),
        )

        # Stay busy for well over one full timeout window.
        for index in range(9):
            await asyncio.sleep(10)
            await filesystem.write(
                key,
                f"/workspace/heartbeat-{index}",
                b"busy",
                expected_sha256=None,
                deadline_at=deadline,
            )

        still_running = await processes.inspect(key, background_id)
        assert still_running.state == ProcessState.RUNNING
        assert await _provider_id(database, key) == provider_id
    finally:
        provider_id = provider_id or await _provider_id(database, key)
        await _kill_exact(provider_id)
        await adapter.close()
        await database.dispose()


async def test_real_e2b_running_process_defers_idle_release(
    tmp_path: Path,
) -> None:
    """Idle cleanup must not pause a sandbox out from under a running process.

    Activity protection is otherwise taken only for the starting operation's
    deadline, so a dev server or long build was released - and killed by
    quiesce - at the idle threshold after the agent's last poll.
    """

    profile = _workspace_profile()
    adapter = _adapter(
        ProfileRegistry((profile,)),
        scope=f"e2b:process-lease:{uuid4()}",
    )
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(database, adapter)
    processes = ProcessExecutionService(database, adapter)
    # Idle cleanup is due immediately, so only the process lease can hold it off.
    maintenance = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
        batch_size=1,
    )
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    provider_id: str | None = None

    try:
        await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
            verify_ready=True,
        )
        provider_id = await _provider_id(database, key)

        background_id = uuid4()
        # Starting a process protects the sandbox only for its own request
        # deadline; a short one here is exactly the case the lease exists for -
        # a process that outlives the call that started it.
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=background_id,
                shell_command="sleep 300",
                argv=None,
                cwd="/workspace",
                environment=(),
                tty=None,
                output_limit_bytes=4096,
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=5),
            ),
        )
        assert await processes.renew_process_leases(lease_seconds=20) == 1

        # Held off purely by the lease: the sandbox is otherwise long idle.
        assert await maintenance.run_once(deadline_at=deadline) == 0
        assert (await processes.inspect(key, background_id)).state == (
            ProcessState.RUNNING
        )

        # Once the process is gone the lease stops being renewed, and after the
        # outstanding protection expires idle cleanup proceeds normally. Note
        # protection only ever extends, so this waits it out rather than
        # shortening it.
        await processes.terminate(
            key, background_id, grace_seconds=0, deadline_at=deadline
        )
        assert await processes.renew_process_leases(lease_seconds=20) == 0
        await asyncio.sleep(21)

        assert await maintenance.run_once(deadline_at=deadline) == 1
        async with database.uow() as uow:
            released = await uow.repository.current_allocation(key)
            await uow.commit()
        assert released is not None
        assert released.state == AllocationState.RELEASED
    finally:
        provider_id = provider_id or await _provider_id(database, key)
        await _kill_exact(provider_id)
        await adapter.close()
        await database.dispose()


async def test_real_e2b_storage_generation_marks_only_a_genuinely_lost_disk(
    tmp_path: Path,
) -> None:
    """The generation must move only when files are actually gone.

    It is the one signal that lets an agent tell a wiped workspace from an
    ordinary empty directory, so a false positive is as harmful as no signal
    at all: it would teach agents to distrust it.
    """

    profile = _workspace_profile()
    adapter = _adapter(
        ProfileRegistry((profile,)),
        scope=f"e2b:storage-generation:{uuid4()}",
    )
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(
        database,
        adapter,
        workspace_retention_seconds=0,
    )
    maintenance = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
        batch_size=1,
    )
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(minutes=6)
    original_provider_id: str | None = None
    recreated_provider_id: str | None = None

    try:
        first = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
            verify_ready=True,
        )
        original_provider_id = await _provider_id(database, key)
        assert first.storage_generation == 0

        # An ordinary idle pause and resume keeps the disk, so the generation
        # must not move: this is the case agents were misreading as a wipe.
        assert await maintenance.run_once(deadline_at=deadline) == 1
        resumed = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
            verify_ready=True,
        )
        assert resumed.storage_generation == 0
        assert await _provider_id(database, key) == original_provider_id

        # Retention expiry genuinely destroys the disk, and that must show.
        assert await maintenance.run_once(deadline_at=deadline) == 1
        assert await maintenance.run_once(deadline_at=deadline) == 1
        recreated = await lifecycle.ensure(
            key,
            profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
            verify_ready=True,
        )
        recreated_provider_id = await _provider_id(database, key)

        assert recreated_provider_id != original_provider_id
        assert recreated.storage_generation == 1
    finally:
        await _kill_exact(original_provider_id)
        await _kill_exact(recreated_provider_id)
        await adapter.close()
        await database.dispose()
