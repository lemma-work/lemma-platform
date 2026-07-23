from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import zipfile

from fastapi import FastAPI, Header, HTTPException, Response
import httpx
import pytest
import uvicorn

from agentbox_client import AgentBoxClient
from agentbox_client import AdmissionClass as ClientAdmissionClass
from agentbox_client import ProcessState as ClientProcessState
from agentbox_client import ProfileRef as ClientProfileRef
from agentbox_client import WorkloadKind as ClientWorkloadKind
from agentbox.adapters.docker import (
    DockerAdapterConfig,
    DockerSandboxAdapter,
    RuntimeCredentialSigner,
)
from agentbox.adapters.docker_engine import DockerEngineClient
from agentbox.adapters.docker_engine import (
    DockerContainerCreateRequest,
    DockerEmptyObject,
    DockerHostConfig,
    DockerNetworkCreateRequest,
    DockerPortBinding,
)
from agentbox.domain import (
    AdmissionClass,
    ByteRange,
    CreatePythonSessionRequest,
    EnvironmentVariable,
    ExecutePythonRequest,
    ProcessState,
    PortProtocol,
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
from agentbox.api.port_proxy import access_router
from agentbox.port_access import PortAccessService, PortAccessSigner
from agentbox.persistence.uow import StateDatabase
from agentbox.processes import ProcessExecutionService
from agentbox.python_sessions import PythonSessionService
from agentbox.profiles import DockerProfileArtifact, ProfileRegistry, SandboxProfile


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("AGENTBOX_RUN_DOCKER_TESTS") != "1",
        reason="set AGENTBOX_RUN_DOCKER_TESTS=1 for real Docker conformance",
    ),
]


def docker_socket() -> str:
    configured = os.getenv("AGENTBOX_DOCKER_SOCKET")
    if configured:
        return configured
    desktop = Path.home() / ".docker" / "run" / "docker.sock"
    if desktop.exists():
        return str(desktop)
    return "/var/run/docker.sock"


def profile(name: str, fill: str) -> SandboxProfile:
    return SandboxProfile(
        ref=SandboxProfileRef(name=name, digest=f"sha256:{fill * 64}"),
        workload_kind=WorkloadKind.WORKSPACE,
        runtime_abi="python-3.12",
        capabilities=frozenset(
            {SandboxCapability.PROCESS, SandboxCapability.FILESYSTEM}
        ),
        allowed_roots=("/workspace",),
        docker=DockerProfileArtifact(
            image="agentbox-workspace:dev",
            command=("sleep", "infinity"),
            readiness_argv=("python", "-c", "pass"),
        ),
        e2b=None,
    )


def function_profile() -> SandboxProfile:
    return SandboxProfile(
        ref=SandboxProfileRef(name="function-python-v1", digest=f"sha256:{'f' * 64}"),
        workload_kind=WorkloadKind.FUNCTION,
        runtime_abi="lemma-function-python-1",
        capabilities=frozenset({SandboxCapability.PROCESS}),
        allowed_roots=("/tmp",),
        docker=DockerProfileArtifact(
            image="agentbox-function:dev",
            command=("sleep", "infinity"),
            readiness_argv=("python", "-c", "pass"),
        ),
        e2b=None,
    )


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


async def test_real_docker_create_profile_replace_and_volume_persistence(
    tmp_path: Path,
):
    first_profile = profile("workspace-python-v1", "a")
    second_profile = profile("workspace-python-v7", "b")
    registry = ProfileRegistry((first_profile, second_profile))
    engine = DockerEngineClient(socket_path=docker_socket())
    adapter = DockerSandboxAdapter(
        engine,
        registry,
        DockerAdapterConfig(
            scope="docker:real-test",
            allow_mutable_images=True,
            memory_bytes=256 * 1024 * 1024,
            nano_cpus=500_000_000,
            pids_limit=128,
        ),
    )
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    service = SandboxLifecycleService(database, adapter)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    container_ids: list[str] = []
    volume_name: str | None = None

    try:
        first = await service.ensure(
            key,
            first_profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
        assert first.ready is True
        assert first.allocation_id is not None
        async with database.uow() as uow:
            first_allocation = await uow.repository.current_allocation(key)
            storage = await uow.repository.get_workspace_storage(key)
            await uow.commit()
        assert first_allocation is not None
        assert first_allocation.provider_id is not None
        assert storage is not None
        assert storage.provider_storage_id is not None
        container_ids.append(first_allocation.provider_id)
        volume_name = storage.provider_storage_id

        write_exit = await engine.run_exec(
            first_allocation.provider_id,
            (
                "python",
                "-c",
                "from pathlib import Path; Path('/workspace/probe').write_bytes(b'ok')",
            ),
            deadline_at=deadline,
        )
        assert write_exit == 0

        second = await service.ensure(
            key,
            second_profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
        assert second.ready is True
        async with database.uow() as uow:
            second_allocation = await uow.repository.current_allocation(key)
            await uow.commit()
        assert second_allocation is not None
        assert second_allocation.provider_id is not None
        container_ids.append(second_allocation.provider_id)
        read_exit = await engine.run_exec(
            second_allocation.provider_id,
            (
                "python",
                "-c",
                "from pathlib import Path; assert Path('/workspace/probe').read_bytes() == b'ok'",
            ),
            deadline_at=deadline,
        )
        assert read_exit == 0
        assert database.active_units_of_work == 0
    finally:
        cleanup_deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
        for container_id in container_ids:
            await engine.delete_container(
                container_id, deadline_at=cleanup_deadline, force=True
            )
        if volume_name is not None:
            await engine.delete_volume(volume_name, deadline_at=cleanup_deadline)
        await adapter.close()
        await database.dispose()


async def test_real_docker_private_network_reaches_unpublished_runtime(
    tmp_path: Path,
):
    del tmp_path
    engine = DockerEngineClient(socket_path=docker_socket())
    test_run = str(uuid4())
    network_name = f"agentbox-private-{uuid4().hex[:12]}"
    manager_name = f"agentbox-private-manager-{uuid4().hex[:12]}"
    provider_scope = f"docker:private-network:{uuid4()}"
    api_key = f"private-network-api-{uuid4()}"
    profile_digest = f"sha256:{'d' * 64}"
    deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
    network = await engine.create_network(
        DockerNetworkCreateRequest(
            name=network_name,
            labels={
                "managed-by": "agentbox-conformance",
                "test-run": test_run,
            },
        ),
        deadline_at=deadline,
    )
    manager_id: str | None = None
    workspace_id = uuid4()
    workspace_provider_id: str | None = None
    workspace_volume_name: str | None = None

    try:
        manager = await engine.create_container(
            manager_name,
            DockerContainerCreateRequest(
                image=os.getenv("AGENTBOX_MANAGER_TEST_IMAGE", "agentbox-manager:dev"),
                labels={
                    "managed-by": "agentbox-conformance-manager",
                    "test-run": test_run,
                },
                user="0",
                working_dir="/app",
                env=(
                    "AGENTBOX_ENVIRONMENT=test",
                    f"AGENTBOX_API_KEY={api_key}",
                    "AGENTBOX_API_URL=http://agentbox-manager:8000",
                    "AGENTBOX_PROVIDER=docker",
                    "AGENTBOX_STATE_DB_PATH=/tmp/agentbox-state.db",
                    "AGENTBOX_AUTO_CREATE_SCHEMA=true",
                    "AGENTBOX_DOCKER_SOCKET_PATH=/var/run/docker.sock",
                    f"AGENTBOX_DOCKER_SCOPE={provider_scope}",
                    "AGENTBOX_DOCKER_ALLOW_MUTABLE_IMAGES=true",
                    f"AGENTBOX_DOCKER_PRIVATE_NETWORK={network_name}",
                    "AGENTBOX_ADD_HOST_GATEWAY=false",
                    "AGENTBOX_WORKSPACE_PROFILE_NAME=workspace-python-v1",
                    f"AGENTBOX_WORKSPACE_PROFILE_DIGEST={profile_digest}",
                    "AGENTBOX_WORKSPACE_IMAGE=agentbox-workspace:dev",
                    "AGENTBOX_FUNCTION_PROFILE_NAME=function-python-v1",
                    f"AGENTBOX_FUNCTION_PROFILE_DIGEST=sha256:{'f' * 64}",
                    "AGENTBOX_FUNCTION_IMAGE=agentbox-function:dev",
                    f"AGENTBOX_RUNTIME_CREDENTIAL_KEY={'n' * 32}",
                    "AGENTBOX_CLEANUP_INTERVAL_SECONDS=30",
                ),
                exposed_ports={"8000/tcp": DockerEmptyObject()},
                host_config=DockerHostConfig(
                    binds=("/var/run/docker.sock:/var/run/docker.sock",),
                    port_bindings={
                        "8000/tcp": (
                            DockerPortBinding(host_ip="127.0.0.1", host_port=""),
                        )
                    },
                    network_mode=network_name,
                    memory=512 * 1024 * 1024,
                    nano_cpus=1_000_000_000,
                    pids_limit=256,
                ),
            ),
            deadline_at=deadline,
        )
        manager_id = manager.container_id
        await engine.start_container(manager_id, deadline_at=deadline)
        manager_inspect = await engine.inspect_container(
            manager_id,
            deadline_at=deadline,
        )
        assert manager_inspect is not None
        manager_ports = manager_inspect.network_settings.ports.get("8000/tcp")
        assert manager_ports
        manager_url = f"http://127.0.0.1:{manager_ports[0].host_port}"

        async with httpx.AsyncClient(timeout=1) as health_client:
            last_health = "no response"
            for _ in range(300):
                manager_inspect = await engine.inspect_container(
                    manager_id,
                    deadline_at=deadline,
                )
                assert manager_inspect is not None
                assert manager_inspect.state.running, manager_inspect.state
                try:
                    response = await health_client.get(f"{manager_url}/health/ready")
                    last_health = f"HTTP {response.status_code}: {response.text[:200]}"
                    if response.status_code == 200:
                        break
                except httpx.HTTPError as exc:
                    last_health = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(0.1)
            else:
                raise AssertionError(f"manager did not become ready: {last_health}")

        async with AgentBoxClient(
            base_url=manager_url,
            api_key=api_key,
            timeout_seconds=120,
        ) as client:
            while True:
                handle = await client.ensure_sandbox(
                    ClientWorkloadKind.WORKSPACE,
                    workspace_id,
                    profile=ClientProfileRef(
                        name="workspace-python-v1",
                        digest=profile_digest,
                    ),
                    admission_class=ClientAdmissionClass.INTERACTIVE,
                    deadline_at=deadline,
                )
                if handle.ready:
                    break
                assert datetime.now(timezone.utc) < deadline
                await asyncio.sleep((handle.retry_after_ms or 100) / 1000)

            inventory = await engine.list_containers(
                labels={
                    "managed-by": "agentbox",
                    "provider-scope": provider_scope,
                    "logical-id": str(workspace_id),
                },
                deadline_at=deadline,
            )
            assert len(inventory) == 1
            workspace_provider_id = inventory[0].container_id
            workspace_inspect = await engine.inspect_container(
                workspace_provider_id,
                deadline_at=deadline,
            )
            assert workspace_inspect is not None
            workspace_volume_name = workspace_inspect.config.labels.get(
                "workspace-storage-id"
            )
            attachment = workspace_inspect.network_settings.networks.get(network_name)
            assert attachment is not None and attachment.ip_address
            assert not workspace_inspect.network_settings.ports.get("8080/tcp")

            operation_id = uuid4()
            await client.start_process(
                ClientWorkloadKind.WORKSPACE,
                workspace_id,
                operation_id=operation_id,
                shell_command="printf 'private-runtime-ok\\n'",
                cwd="/workspace",
                deadline_at=deadline,
            )
            output = await client.read_process_output(
                ClientWorkloadKind.WORKSPACE,
                workspace_id,
                operation_id,
                wait_seconds=10,
                deadline_at=deadline,
            )
            output_chunks = list(output.chunks)
            if output.state == ClientProcessState.RUNNING:
                output = await client.read_process_output(
                    ClientWorkloadKind.WORKSPACE,
                    workspace_id,
                    operation_id,
                    after_sequence=output.next_sequence,
                    wait_seconds=10,
                    deadline_at=deadline,
                )
                output_chunks.extend(output.chunks)
            assert output.state == ClientProcessState.SUCCEEDED
            assert b"private-runtime-ok" in b"".join(
                chunk.data for chunk in output_chunks
            )

            await client.destroy_sandbox(
                ClientWorkloadKind.WORKSPACE,
                workspace_id,
                deadline_at=deadline,
            )

        assert (
            await engine.inspect_container(
                workspace_provider_id,
                deadline_at=deadline,
            )
            is None
        )
        if workspace_volume_name is not None:
            assert (
                await engine.inspect_volume(
                    workspace_volume_name,
                    deadline_at=deadline,
                )
                is None
            )
        assert not await engine.list_containers(
            labels={
                "managed-by": "agentbox",
                "provider-scope": provider_scope,
            },
            deadline_at=deadline,
        )
    finally:
        cleanup_deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
        inventory = await engine.list_containers(
            labels={
                "managed-by": "agentbox",
                "provider-scope": provider_scope,
            },
            deadline_at=cleanup_deadline,
        )
        cleanup_volumes = {
            inspected.config.labels["workspace-storage-id"]
            for item in inventory
            if (
                (
                    inspected := await engine.inspect_container(
                        item.container_id,
                        deadline_at=cleanup_deadline,
                    )
                )
                is not None
                and "workspace-storage-id" in inspected.config.labels
            )
        }
        for item in inventory:
            await engine.delete_container(
                item.container_id,
                deadline_at=cleanup_deadline,
                force=True,
            )
        for volume_name in cleanup_volumes:
            await engine.delete_volume(volume_name, deadline_at=cleanup_deadline)
        manager_inventory = await engine.list_containers(
            labels={
                "managed-by": "agentbox-conformance-manager",
                "test-run": test_run,
            },
            deadline_at=cleanup_deadline,
        )
        for item in manager_inventory:
            await engine.delete_container(
                item.container_id,
                deadline_at=cleanup_deadline,
                force=True,
            )
        await engine.delete_network(
            network.network_id,
            deadline_at=cleanup_deadline,
        )
        await engine.close()


async def test_real_docker_runtime_process_pty_input_resize_and_reconnect(
    tmp_path: Path,
):
    workspace_profile = SandboxProfile(
        ref=SandboxProfileRef(name="workspace-python-v1", digest=f"sha256:{'c' * 64}"),
        workload_kind=WorkloadKind.WORKSPACE,
        runtime_abi="python-3.12",
        capabilities=frozenset(
            {
                SandboxCapability.PROCESS,
                SandboxCapability.PTY,
                SandboxCapability.FILESYSTEM,
                SandboxCapability.PORT_ACCESS,
                SandboxCapability.BROWSER,
            }
        ),
        allowed_roots=("/workspace",),
        docker=DockerProfileArtifact(
            image="agentbox-workspace:dev",
            command=(),
            readiness_argv=("python", "-c", "pass"),
            published_ports=(8080, 4848),
            runtime_port=8080,
        ),
        e2b=None,
    )
    registry = ProfileRegistry((workspace_profile,))
    engine = DockerEngineClient(socket_path=docker_socket())
    adapter = DockerSandboxAdapter(
        engine,
        registry,
        DockerAdapterConfig(
            scope="docker:runtime-test",
            allow_mutable_images=True,
            memory_bytes=2 * 1024 * 1024 * 1024,
            nano_cpus=1_000_000_000,
            pids_limit=512,
        ),
        runtime_credentials=RuntimeCredentialSigner(b"r" * 32),
    )
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(database, adapter)
    processes = ProcessExecutionService(database, adapter)
    filesystem = FilesystemService(database, adapter)
    python_sessions = PythonSessionService(database, adapter)
    port_access = PortAccessService(
        database,
        adapter,
        PortAccessSigner(b"p" * 32),
        public_base_url="http://agentbox.test",
    )
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    # A clean linux/amd64 image on an emulated arm64 development host can spend
    # well over a minute starting Chromium. This deadline covers the complete
    # multi-capability scenario; individual long polls remain bounded to 20s.
    deadline = datetime.now(timezone.utc) + timedelta(seconds=180)
    provider_id: str | None = None
    volume_name: str | None = None

    try:
        handle = await lifecycle.ensure(
            key,
            workspace_profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
        async with database.uow() as uow:
            allocation = await uow.repository.current_allocation(key)
            if allocation is None and handle.allocation_id is not None:
                allocation = await uow.repository.get_allocation_by_id(
                    handle.allocation_id
                )
            storage = await uow.repository.get_workspace_storage(key)
            await uow.commit()
        assert allocation is not None and allocation.provider_id is not None
        assert storage is not None and storage.provider_storage_id is not None
        provider_id = allocation.provider_id
        volume_name = storage.provider_storage_id
        assert handle.ready is True

        operation_id = uuid4()
        started, created = await processes.start(
            key,
            StartProcessRequest(
                operation_id=operation_id,
                shell_command=(
                    "read line; size=$(stty size); "
                    "printf 'value:%s size:%s token:%s\\n' "
                    '"$line" "$size" "$TEST_TOKEN"'
                ),
                argv=None,
                cwd="/workspace",
                environment=(EnvironmentVariable("TEST_TOKEN", "not-persisted"),),
                tty=TerminalSize(cols=80, rows=24),
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        assert created is True
        await processes.resize(
            key,
            operation_id,
            TerminalSize(cols=100, rows=40),
            deadline_at=deadline,
        )
        await processes.send_input(key, operation_id, b"hello\n", deadline_at=deadline)
        output = None
        for _ in range(100):
            output = await processes.read_output(
                key,
                operation_id,
                after_sequence=0,
                wait_seconds=0.1,
                deadline_at=deadline,
            )
            if output.state != ProcessState.RUNNING:
                break
        assert output is not None
        combined = b"".join(chunk.data for chunk in output.chunks)
        last_sequence = max(chunk.sequence for chunk in output.chunks)
        reconnected = await processes.read_output(
            key,
            operation_id,
            after_sequence=last_sequence,
            wait_seconds=0,
            deadline_at=deadline,
        )

        assert started.operation_id == operation_id
        assert output.state == ProcessState.SUCCEEDED
        assert b"value:hello size:40 100 token:not-persisted" in combined
        assert reconnected.chunks == ()

        binary_payload = b"\x00agentbox\xff" * 128 * 1024
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
            ByteRange(offset=17, length=8193),
            deadline_at=deadline,
        )
        listed = await filesystem.list(key, "/workspace", deadline_at=deadline)
        await filesystem.move(
            key,
            "/workspace/binary.dat",
            "/workspace/moved.dat",
            deadline_at=deadline,
        )
        moved = await filesystem.stat(key, "/workspace/moved.dat", deadline_at=deadline)
        deleted = await filesystem.delete(
            key,
            "/workspace/moved.dat",
            recursive=False,
            deadline_at=deadline,
        )

        assert written.sha256 is not None
        assert written.size_bytes == len(binary_payload)
        assert ranged == binary_payload[17 : 17 + 8193]
        assert any(entry.path == "/workspace/binary.dat" for entry in listed)
        assert moved.sha256 is None
        assert deleted is True

        large_chunk = b"s" * (1024 * 1024)
        large_chunk_count = 65

        async def large_chunks():
            for _ in range(large_chunk_count):
                yield large_chunk

        large_written = await filesystem.write_stream(
            key,
            "/workspace/streamed-large.bin",
            large_chunks(),
            expected_sha256=None,
            deadline_at=deadline,
        )
        large_stream = await filesystem.open_read(
            key,
            "/workspace/streamed-large.bin",
            ByteRange(offset=0, length=None),
            deadline_at=deadline,
        )
        large_size = 0
        large_digest = hashlib.sha256()
        async for chunk in large_stream:
            large_size += len(chunk)
            large_digest.update(chunk)

        expected_large_digest = hashlib.sha256()
        for _ in range(large_chunk_count):
            expected_large_digest.update(large_chunk)
        assert large_written.size_bytes == large_chunk_count * len(large_chunk)
        assert large_written.sha256 == f"sha256:{expected_large_digest.hexdigest()}"
        assert large_size == large_written.size_bytes
        assert large_digest.hexdigest() == expected_large_digest.hexdigest()
        assert await filesystem.delete(
            key,
            "/workspace/streamed-large.bin",
            recursive=False,
            deadline_at=deadline,
        )

        session_id = uuid4()
        python_session_request = CreatePythonSessionRequest(
            session_id=session_id,
            cwd="/workspace",
            environment_keys=("PYTHON_MARK",),
            deadline_at=deadline,
        )
        session, session_created = await python_sessions.create(
            key,
            python_session_request,
        )
        await python_sessions.execute(
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
        python_result, python_created = await python_sessions.execute(
            key,
            session_id,
            ExecutePythonRequest(
                operation_id=uuid4(),
                code=(
                    "import os\n"
                    "os.write(1, b'native-python\\n')\n"
                    "(value + 2, os.environ['PYTHON_MARK'])"
                ),
                environment=(EnvironmentVariable("PYTHON_MARK", "docker"),),
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )

        assert session_created is True
        assert session.session_id == session_id
        assert python_created is True
        assert python_result.state == PythonExecutionState.SUCCEEDED
        assert python_result.stdout == "native-python\n"
        assert python_result.result == "(42, 'docker')"

        await filesystem.write(
            key,
            "/workspace/browser.html",
            b"<!doctype html><title>AgentBox browser</title>"
            b"<button id='probe'>browser-ready</button>",
            expected_sha256=None,
            deadline_at=deadline,
        )
        browser_start_id = uuid4()
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=browser_start_id,
                shell_command=None,
                argv=(
                    "start-browser",
                    "file:///workspace/browser.html",
                ),
                cwd="/workspace",
                environment=(),
                tty=None,
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        browser_started, browser_start_output = await _read_terminal(
            processes,
            key,
            browser_start_id,
            deadline_at=deadline,
        )
        assert browser_started.state == ProcessState.SUCCEEDED, browser_start_output

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
            processes,
            key,
            snapshot_id,
            deadline_at=deadline,
        )
        assert snapshot.state == ProcessState.SUCCEEDED, snapshot_output
        assert b"browser-ready" in snapshot_output

        grant = await port_access.create(
            key,
            port=4848,
            protocol=PortProtocol.HTTP,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        token = urlsplit(grant.url).path.split("/")[2]
        _claims, browser_target = await port_access.resolve(token, deadline_at=deadline)
        async with httpx.AsyncClient(timeout=10) as browser_client:
            health = await browser_client.get(
                f"{browser_target.base_url}/health",
                headers={item.name: item.value for item in browser_target.headers},
            )
        assert health.status_code == 200

        proxy_app = FastAPI()
        proxy_app.state.port_access = port_access
        proxy_app.include_router(access_router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=proxy_app),
            base_url="http://agentbox.test",
        ) as proxy_client:
            proxied_health = await proxy_client.get(f"/port-access/{token}/health")
        assert proxied_health.status_code == 200

        await filesystem.write(
            key,
            "/workspace/release-persistence",
            b"survives",
            expected_sha256=None,
            deadline_at=deadline,
        )
        released = await lifecycle.release(key, deadline_at=deadline)
        stopped = await engine.inspect_container(provider_id, deadline_at=deadline)
        resumed = await lifecycle.ensure(
            key,
            workspace_profile.ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
        persisted = await filesystem.read(
            key,
            "/workspace/release-persistence",
            ByteRange(offset=0, length=None),
            deadline_at=deadline,
        )
        stale_session = await python_sessions.inspect(key, session_id)
        replacement_session, replacement_created = await python_sessions.create(
            key, python_session_request
        )
        reset_result, _ = await python_sessions.execute(
            key,
            session_id,
            ExecutePythonRequest(
                operation_id=uuid4(),
                code="value",
                environment=(),
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )

        assert released.ready is False
        assert stopped is not None and stopped.state.running is False
        assert resumed.ready is True
        assert resumed.allocation_id == handle.allocation_id
        assert resumed.allocation_epoch == handle.allocation_epoch + 1
        assert persisted == b"survives"
        assert stale_session.state == PythonSessionState.STALE
        assert replacement_created is True
        assert replacement_session.state == PythonSessionState.ACTIVE
        assert reset_result.error_name == "NameError"

        assert await lifecycle.destroy(key, deadline_at=deadline)
        assert await engine.inspect_container(provider_id, deadline_at=deadline) is None
        assert await engine.inspect_volume(volume_name, deadline_at=deadline) is None
        assert database.active_units_of_work == 0
    finally:
        cleanup_deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
        if provider_id is not None:
            await engine.delete_container(
                provider_id, deadline_at=cleanup_deadline, force=True
            )
        if volume_name is not None:
            await engine.delete_volume(volume_name, deadline_at=cleanup_deadline)
        await adapter.close()
        await database.dispose()


async def test_real_docker_function_process_input_reconnect_and_process_group_cancel(
    tmp_path: Path,
):
    sandbox_profile = function_profile()
    registry = ProfileRegistry((sandbox_profile,))
    engine = DockerEngineClient(socket_path=docker_socket())
    adapter = DockerSandboxAdapter(
        engine,
        registry,
        DockerAdapterConfig(
            scope="docker:function-runtime-test",
            allow_mutable_images=True,
            memory_bytes=256 * 1024 * 1024,
            nano_cpus=500_000_000,
            pids_limit=128,
        ),
    )
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(database, adapter)
    processes = ProcessExecutionService(database, adapter)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
    provider_id: str | None = None

    try:
        handle = await lifecycle.ensure(
            key,
            sandbox_profile.ref,
            admission_class=AdmissionClass.LATENCY,
            deadline_at=deadline,
        )
        assert handle.ready is True
        async with database.uow() as uow:
            allocation = await uow.repository.current_allocation(key)
            await uow.commit()
        assert allocation is not None and allocation.provider_id is not None
        provider_id = allocation.provider_id

        operation_id = uuid4()
        started, created = await processes.start(
            key,
            StartProcessRequest(
                operation_id=operation_id,
                shell_command='read ticket; printf \'ticket:%s secret:%s\\n\' "$ticket" "$SECRET"',
                argv=None,
                cwd="/tmp",
                environment=(EnvironmentVariable("SECRET", "ephemeral"),),
                tty=None,
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        assert created is True
        await processes.send_input(
            key, operation_id, b"single-use-ticket\n", deadline_at=deadline
        )
        result = await processes.read_output(
            key,
            operation_id,
            after_sequence=0,
            wait_seconds=10,
            deadline_at=deadline,
        )
        assert result.state == ProcessState.SUCCEEDED
        assert b"ticket:single-use-ticket secret:ephemeral" in b"".join(
            chunk.data for chunk in result.chunks
        )
        assert started.provider_process_id is not None
        reconnected = await processes.read_output(
            key,
            operation_id,
            after_sequence=result.next_sequence,
            wait_seconds=0,
            deadline_at=deadline,
        )
        assert reconnected.chunks == ()
        assert reconnected.state == ProcessState.SUCCEEDED

        cancellation_id = uuid4()
        await processes.start(
            key,
            StartProcessRequest(
                operation_id=cancellation_id,
                shell_command='sleep 60 & child=$!; printf \'child:%s\\n\' "$child"; wait "$child"',
                argv=None,
                cwd="/tmp",
                environment=(),
                tty=None,
                output_limit_bytes=65536,
                deadline_at=deadline,
            ),
        )
        running = await processes.read_output(
            key,
            cancellation_id,
            after_sequence=0,
            wait_seconds=5,
            deadline_at=deadline,
        )
        child_line = b"".join(chunk.data for chunk in running.chunks).decode()
        child_pid = int(child_line.strip().split(":", 1)[1])
        await processes.terminate(
            key,
            cancellation_id,
            grace_seconds=0.1,
            deadline_at=deadline,
        )
        cancelled = await processes.read_output(
            key,
            cancellation_id,
            after_sequence=running.next_sequence,
            wait_seconds=0,
            deadline_at=deadline,
        )
        assert cancelled.state == ProcessState.CANCELLED
        descendant_check = await engine.run_exec(
            provider_id,
            ("bash", "-lc", f"! kill -0 {child_pid} 2>/dev/null"),
            working_dir="/tmp",
            deadline_at=deadline,
        )
        assert descendant_check == 0
        assert database.active_units_of_work == 0
        assert await lifecycle.destroy(key, deadline_at=deadline)
        assert await engine.inspect_container(provider_id, deadline_at=deadline) is None
    finally:
        cleanup_deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
        if provider_id is not None:
            await engine.delete_container(
                provider_id, deadline_at=cleanup_deadline, force=True
            )
        await adapter.close()
        await database.dispose()


def _function_artifact(function_name: str) -> bytes:
    source = f"""#input_type_name: FunctionInput
#output_type_name: FunctionOutput
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class FunctionInput(BaseModel):
    value: int

class FunctionOutput(BaseModel):
    value: int

async def {function_name}(ctx: FunctionContext, data: FunctionInput) -> FunctionOutput:
    print(f'executed-for:{{ctx.user_id}}')
    return FunctionOutput(value=data.value + 1)
"""
    manifest = {
        "format_version": 1,
        "runtime_abi": "lemma-function-python-1",
        "builder_digest": "docker-real-test-builder",
        "dependency_lock": [],
        "source_path": "function.py",
        "input_model": "FunctionInput",
        "output_model": "FunctionOutput",
        "entrypoint": function_name,
        "config_model": None,
        "dependency_path": None,
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        archive.writestr("function.py", source)
    return output.getvalue()


async def test_real_docker_function_runner_recovers_lost_terminal_response(
    tmp_path: Path,
):
    function_name = "increment"
    artifact = _function_artifact(function_name)
    artifact_sha256 = f"sha256:{hashlib.sha256(artifact).hexdigest()}"
    attempt_id = uuid4()
    user_id = uuid4()
    pod_id = uuid4()
    function_id = uuid4()
    ticket = f"ticket-{uuid4()}"
    runtime_token = f"runtime-{uuid4()}-{uuid4()}"
    events: list[tuple[str, dict]] = []
    claimed = False
    terminal_attempts = 0
    terminal_payload: dict | None = None
    terminal_event = asyncio.Event()
    gateway = FastAPI()

    @gateway.post("/internal/function-runtime/attempts:claim")
    async def claim(authorization: str | None = Header(default=None)):
        nonlocal claimed
        if authorization != f"Bearer {ticket}" or claimed:
            raise HTTPException(status_code=409, detail="ticket already claimed")
        claimed = True
        return {
            "attempt_id": str(attempt_id),
            "fence": 1,
            "runtime_token": runtime_token,
            "artifact_url": f"/internal/function-runtime/attempts/{attempt_id}/artifact",
            "artifact_sha256": artifact_sha256,
            "input_data": {"value": 41},
            "config": None,
            "identity": {
                "user_id": str(user_id),
                "user_email": "function@example.test",
                "pod_id": str(pod_id),
                "function_id": str(function_id),
                "function_name": function_name,
                "organization_id": None,
            },
            "lemma_token": "delegated-lemma-token",
            "lemma_base_url": "http://lemma.invalid",
            "deadline_at": (
                datetime.now(timezone.utc) + timedelta(seconds=30)
            ).isoformat(),
        }

    @gateway.get("/internal/function-runtime/attempts/{requested_attempt_id}/artifact")
    async def get_artifact(
        requested_attempt_id: str,
        authorization: str | None = Header(default=None),
    ):
        if (
            requested_attempt_id != str(attempt_id)
            or authorization != f"Bearer {runtime_token}"
        ):
            raise HTTPException(status_code=403)
        return Response(content=artifact, media_type="application/zip")

    @gateway.post(
        "/internal/function-runtime/attempts/{requested_attempt_id}:{event_name}"
    )
    async def report(
        requested_attempt_id: str,
        event_name: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        nonlocal terminal_attempts, terminal_payload
        if (
            requested_attempt_id != str(attempt_id)
            or authorization != f"Bearer {runtime_token}"
        ):
            raise HTTPException(status_code=403)
        if event_name == "terminal":
            terminal_attempts += 1
            if terminal_payload is None:
                terminal_payload = payload
                events.append((event_name, payload))
                return Response(
                    status_code=503,
                    headers={"Retry-After": "0.05"},
                )
            assert payload == terminal_payload
            terminal_event.set()
            return {"accepted": True, "duplicate": True}
        events.append((event_name, payload))
        return {"accepted": True, "duplicate": False}

    config = uvicorn.Config(
        gateway,
        host="0.0.0.0",
        port=0,
        lifespan="off",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        if server_task.done():
            raise RuntimeError("function gateway failed to start")
        await asyncio.sleep(0.01)
    gateway_port = server.servers[0].sockets[0].getsockname()[1]

    sandbox_profile = function_profile()
    registry = ProfileRegistry((sandbox_profile,))
    engine = DockerEngineClient(socket_path=docker_socket())
    adapter = DockerSandboxAdapter(
        engine,
        registry,
        DockerAdapterConfig(
            scope="docker:function-runner-test",
            allow_mutable_images=True,
            memory_bytes=512 * 1024 * 1024,
            nano_cpus=1_000_000_000,
            pids_limit=256,
            add_host_gateway=True,
        ),
    )
    database = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await database.create_schema_for_test()
    lifecycle = SandboxLifecycleService(database, adapter)
    processes = ProcessExecutionService(database, adapter)
    key = SandboxKey(WorkloadKind.FUNCTION, pod_id)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
    provider_id: str | None = None

    try:
        handle = await lifecycle.ensure(
            key,
            sandbox_profile.ref,
            admission_class=AdmissionClass.LATENCY,
            deadline_at=deadline,
        )
        assert handle.ready is True
        async with database.uow() as uow:
            allocation = await uow.repository.current_allocation(key)
            await uow.commit()
        assert allocation is not None and allocation.provider_id is not None
        provider_id = allocation.provider_id

        operation_id = uuid4()
        request = StartProcessRequest(
            operation_id=operation_id,
            shell_command=None,
            argv=("lemma-function-runtime", "execute"),
            cwd="/tmp",
            environment=(
                EnvironmentVariable(
                    "LEMMA_FUNCTION_GATEWAY_URL",
                    f"http://host.docker.internal:{gateway_port}",
                ),
            ),
            tty=None,
            output_limit_bytes=65536,
            deadline_at=deadline,
        )
        _process, created = await processes.start(key, request)
        assert created is True
        await processes.send_input(
            key, operation_id, f"{ticket}\n".encode(), deadline_at=deadline
        )
        await asyncio.wait_for(terminal_event.wait(), timeout=30)
        snapshot = await processes.read_output(
            key,
            operation_id,
            after_sequence=0,
            wait_seconds=10,
            deadline_at=deadline,
        )
        if snapshot.state == ProcessState.RUNNING:
            snapshot = await processes.read_output(
                key,
                operation_id,
                after_sequence=snapshot.next_sequence,
                wait_seconds=10,
                deadline_at=deadline,
            )
        assert snapshot.state == ProcessState.SUCCEEDED
        duplicate, duplicate_created = await processes.start(key, request)
        assert duplicate_created is False
        assert duplicate.operation_id == operation_id

        assert [event for event, _payload in events] == ["started", "terminal"]
        assert terminal_attempts == 2
        terminal = events[-1][1]
        assert terminal["status"] == "completed"
        assert terminal["output_data"] == {"value": 42}
        assert f"executed-for:{user_id}" in terminal["stdout"]
        assert ticket not in json.dumps(terminal)
        assert database.active_units_of_work == 0
    finally:
        cleanup_deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
        if provider_id is not None:
            await engine.delete_container(
                provider_id, deadline_at=cleanup_deadline, force=True
            )
        await adapter.close()
        await database.dispose()
        server.should_exit = True
        await server_task
