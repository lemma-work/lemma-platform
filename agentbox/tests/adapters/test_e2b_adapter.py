from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from e2b import RateLimitException
from e2b.sandbox.filesystem.filesystem import EntryInfo, FileType
import pytest
import pytest_asyncio

from agentbox.adapters.e2b import E2BAdapterConfig, E2BSandboxAdapter
from agentbox.domain import (
    AdmissionClass,
    ByteRange,
    CreatePythonSessionRequest,
    EnvironmentVariable,
    ExecutePythonRequest,
    ProcessState,
    PythonExecutionState,
    SandboxCapability,
    SandboxKey,
    SandboxProfileRef,
    StartProcessRequest,
    StorageKind,
    WorkloadKind,
)
from agentbox.filesystem import FilesystemService
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.processes import ProcessExecutionService
from agentbox.ports import ProviderMetadataEntry
from agentbox.profiles import E2BProfileArtifact, ProfileRegistry, SandboxProfile
from agentbox.python_sessions import PythonSessionService


pytestmark = pytest.mark.asyncio


@dataclass
class FakeInfo:
    sandbox_id: str
    template_id: str
    metadata: dict[str, str]
    state: str = "running"


@dataclass
class FakeProcessInfo:
    pid: int
    envs: dict[str, str]


class FakeHandle:
    def __init__(self, pid: int, result: SimpleNamespace) -> None:
        self.pid = pid
        self._result = result
        self.killed = False

    async def wait(self):
        return self._result

    async def kill(self) -> bool:
        self.killed = True
        return True


class FakeCommands:
    def __init__(self, sandbox: FakeSandbox) -> None:
        self.sandbox = sandbox
        self.stdin: list[tuple[int, bytes]] = []

    async def run(self, command: str, **kwargs):
        pid = self.sandbox.next_pid
        self.sandbox.next_pid += 1
        self.sandbox.processes[pid] = FakeProcessInfo(pid, dict(kwargs["envs"]))
        await kwargs["on_stdout"]("native-out\n")
        await kwargs["on_stderr"]("native-err\n")
        self.sandbox.processes.pop(pid)
        operation_id = kwargs["envs"]["AGENTBOX_OPERATION_ID"]
        self.sandbox.files.data[f"/tmp/.agentbox/processes/{operation_id}/exit"] = b"0"
        return FakeHandle(pid, SimpleNamespace(exit_code=0))

    async def list(self, **_kwargs):
        return list(self.sandbox.processes.values())

    async def send_stdin(self, pid: int, data: bytes, **_kwargs):
        self.stdin.append((pid, data))

    async def kill(self, pid: int, **_kwargs):
        self.sandbox.processes.pop(pid, None)
        return True

    async def connect(self, pid: int, **_kwargs):
        return FakeHandle(pid, SimpleNamespace(exit_code=0))


class FakePty:
    def __init__(self, sandbox: FakeSandbox) -> None:
        self.sandbox = sandbox
        self.stdin: list[tuple[int, bytes]] = []
        self.resizes: list[tuple[int, int, int]] = []

    async def create(self, _size, **kwargs):
        pid = self.sandbox.next_pid
        self.sandbox.next_pid += 1
        self.sandbox.processes[pid] = FakeProcessInfo(pid, dict(kwargs["envs"]))
        await kwargs["on_data"](b"pty-ready\n")
        return FakeHandle(pid, SimpleNamespace(exit_code=0))

    async def connect(self, pid: int, **_kwargs):
        return FakeHandle(pid, SimpleNamespace(exit_code=0))

    async def send_stdin(self, pid: int, data: bytes, **_kwargs):
        self.stdin.append((pid, data))

    async def resize(self, pid: int, size, **_kwargs):
        self.resizes.append((pid, size.cols, size.rows))

    async def kill(self, pid: int, **_kwargs):
        self.sandbox.processes.pop(pid, None)
        return True


class FakeFileStream:
    def __init__(self, data: bytes, *, chunk_size: int = 3) -> None:
        self._chunks = tuple(
            data[offset : offset + chunk_size]
            for offset in range(0, len(data), chunk_size)
        )

    async def __aenter__(self) -> FakeFileStream:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            yield chunk


class FakeFiles:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.directories: set[str] = set()

    async def make_dir(self, path: str, **_kwargs) -> bool:
        self.directories.add(path)
        return True

    async def exists(self, path: str, **_kwargs) -> bool:
        prefix = path.rstrip("/") + "/"
        return path in self.data or any(item.startswith(prefix) for item in self.data)

    async def read(self, path: str, format: str = "text", **_kwargs):
        value = self.data[path]
        if format == "stream":
            return FakeFileStream(value)
        return bytearray(value) if format == "bytes" else value.decode()

    async def write(self, path: str, data: bytes, **_kwargs):
        self.data[path] = bytes(data)
        return self._info(path)

    async def get_info(self, path: str, **_kwargs):
        if path in self.data:
            return self._info(path)
        prefix = path.rstrip("/") + "/"
        if any(item.startswith(prefix) for item in self.data):
            return self._info(path, directory=True)
        raise KeyError(path)

    async def list(self, path: str, **_kwargs):
        prefix = path.rstrip("/") + "/"
        return [
            self._info(item) for item in sorted(self.data) if item.startswith(prefix)
        ]

    async def rename(self, source: str, destination: str, **_kwargs):
        self.data[destination] = self.data.pop(source)
        return self._info(destination)

    async def remove(self, path: str, **_kwargs):
        prefix = path.rstrip("/") + "/"
        self.data.pop(path, None)
        for item in tuple(self.data):
            if item.startswith(prefix):
                self.data.pop(item)

    def _info(self, path: str, directory: bool = False) -> EntryInfo:
        return EntryInfo(
            name=path.rsplit("/", 1)[-1],
            type=FileType.DIR if directory else FileType.FILE,
            path=path,
            size=0 if directory else len(self.data.get(path, b"")),
            mode=0o755 if directory else 0o644,
            permissions="rwxr-xr-x" if directory else "rw-r--r--",
            owner="user",
            group="user",
            modified_time=datetime.now(timezone.utc),
        )


class FakeContext:
    def __init__(self, context_id: str, cwd: str) -> None:
        self.id = context_id
        self.cwd = cwd
        self.language = "python"


class FakePaginator:
    def __init__(self, items: list[FakeInfo]) -> None:
        self._items = items
        self._consumed = False

    @property
    def has_next(self) -> bool:
        return not self._consumed

    async def next_items(self, **_kwargs):
        self._consumed = True
        return self._items


class FakeSandbox:
    instances: dict[str, FakeSandbox] = {}
    infos: dict[str, FakeInfo] = {}
    create_calls = 0
    connect_calls = 0
    kill_calls: list[str] = []
    fail_rate_limit = False
    last_create_kwargs: dict[str, object] = {}

    def __init__(self, sandbox_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.traffic_access_token = "traffic-token"
        self.commands = FakeCommands(self)
        self.pty = FakePty(self)
        self.files = FakeFiles()
        self.processes: dict[int, FakeProcessInfo] = {}
        self.next_pid = 100
        self.contexts: dict[str, FakeContext] = {}
        self.paused = False
        self.timeout = 300
        self.last_python_env: dict[str, str] = {}

    @classmethod
    def reset(cls) -> None:
        cls.instances = {}
        cls.infos = {}
        cls.create_calls = 0
        cls.connect_calls = 0
        cls.kill_calls = []
        cls.fail_rate_limit = False
        cls.last_create_kwargs = {}

    @classmethod
    async def create(cls, *, template: str, metadata: dict[str, str], **kwargs):
        cls.create_calls += 1
        cls.last_create_kwargs = dict(kwargs)
        if cls.fail_rate_limit:
            raise RateLimitException("429")
        sandbox_id = f"e2b-{cls.create_calls}"
        sandbox = cls(sandbox_id)
        cls.instances[sandbox_id] = sandbox
        cls.infos[sandbox_id] = FakeInfo(
            sandbox_id, template.split(":", 1)[0], dict(metadata)
        )
        return sandbox

    @classmethod
    async def connect(cls, sandbox_id: str, **_kwargs):
        cls.connect_calls += 1
        sandbox = cls.instances[sandbox_id]
        sandbox.paused = False
        return sandbox

    @classmethod
    async def kill(cls, sandbox_id: str, **_kwargs):
        cls.kill_calls.append(sandbox_id)
        cls.instances.pop(sandbox_id, None)
        cls.infos.pop(sandbox_id, None)
        return True

    @classmethod
    def list(cls, *, query, **_kwargs):
        expected = query.metadata or {}
        items = [
            info
            for info in cls.infos.values()
            if all(info.metadata.get(name) == value for name, value in expected.items())
        ]
        return FakePaginator(items)

    async def get_info(self, **_kwargs):
        return self.infos[self.sandbox_id]

    async def is_running(self, **_kwargs):
        return not self.paused

    async def set_timeout(self, timeout: int, **_kwargs):
        self.timeout = timeout

    async def pause(self, **_kwargs):
        self.paused = True
        return True

    async def create_code_context(self, *, cwd: str, **_kwargs):
        context = FakeContext(f"context-{len(self.contexts) + 1}", cwd)
        self.contexts[context.id] = context
        return context

    async def list_code_contexts(self):
        return list(self.contexts.values())

    async def restart_code_context(self, _context):
        return None

    async def remove_code_context(self, context):
        context_id = context.id if hasattr(context, "id") else context
        self.contexts.pop(context_id, None)

    async def run_code(self, _code: str, **kwargs):
        self.last_python_env = dict(kwargs["envs"])
        return SimpleNamespace(
            results=[SimpleNamespace(text="42", is_main_result=True)],
            logs=SimpleNamespace(stdout=["python-out\n"], stderr=[]),
            error=None,
        )

    def get_host(self, port: int) -> str:
        return f"{port}-{self.sandbox_id}.e2b.test"


def profile() -> SandboxProfile:
    ref = SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}")
    return SandboxProfile(
        ref=ref,
        workload_kind=WorkloadKind.WORKSPACE,
        runtime_abi="python-3.12",
        capabilities=frozenset(
            {
                SandboxCapability.PROCESS,
                SandboxCapability.PTY,
                SandboxCapability.PYTHON_SESSION,
                SandboxCapability.FILESYSTEM,
            }
        ),
        allowed_roots=("/workspace", "/tmp"),
        docker=None,
        e2b=E2BProfileArtifact(template_id="template-1", build_id="template-1"),
    )


def adapter() -> E2BSandboxAdapter:
    return E2BSandboxAdapter(
        ProfileRegistry((profile(),)),
        E2BAdapterConfig(api_key="e2b_" + "0" * 40, scope="e2b:test"),
        sandbox_class=FakeSandbox,
    )


def function_profile() -> SandboxProfile:
    return SandboxProfile(
        ref=SandboxProfileRef("function-python-v1", f"sha256:{'b' * 64}"),
        workload_kind=WorkloadKind.FUNCTION,
        runtime_abi="lemma-function-python-1",
        capabilities=frozenset({SandboxCapability.PROCESS}),
        allowed_roots=("/tmp",),
        docker=None,
        e2b=E2BProfileArtifact(template_id="function-template", build_id="build-1"),
    )


async def test_function_egress_is_restricted_to_configured_gateway(
    database: StateDatabase,
) -> None:
    selected_profile = function_profile()
    provider = E2BSandboxAdapter(
        ProfileRegistry((selected_profile,)),
        E2BAdapterConfig(
            api_key="e2b_" + "0" * 40,
            scope="e2b:function-test",
            function_allow_out=("benchmark.trycloudflare.com",),
        ),
        sandbox_class=FakeSandbox,
    )
    lifecycle = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())

    handle = await lifecycle.ensure(
        key,
        selected_profile.ref,
        admission_class=AdmissionClass.LATENCY,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )

    assert handle.ready is True
    assert FakeSandbox.last_create_kwargs["allow_internet_access"] is True
    assert FakeSandbox.last_create_kwargs["network"] == {
        "allow_public_traffic": False,
        "allow_out": ["benchmark.trycloudflare.com"],
        "deny_out": ["0.0.0.0/0"],
    }


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeSandbox.reset()


@pytest_asyncio.fixture
async def database(tmp_path):
    state = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await state.create_schema_for_test()
    try:
        yield state
    finally:
        await state.dispose()


async def provision(database: StateDatabase):
    provider = adapter()
    lifecycle = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    handle = await lifecycle.ensure(
        key,
        profile().ref,
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    return provider, lifecycle, key, deadline, handle


async def test_workspace_uses_exact_pause_resume_identity_and_native_storage(
    database: StateDatabase,
) -> None:
    provider, lifecycle, key, deadline, created = await provision(database)
    provider_id = next(iter(FakeSandbox.instances))

    await FilesystemService(database, provider).write(
        key,
        "/workspace/state.bin",
        b"persistent",
        expected_sha256=None,
        deadline_at=deadline,
    )
    released = await lifecycle.release(key, deadline_at=deadline)
    resumed = await lifecycle.ensure(
        key,
        profile().ref,
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    data = await FilesystemService(database, provider).read(
        key,
        "/workspace/state.bin",
        ByteRange(0, None),
        deadline_at=deadline,
    )
    stat = await FilesystemService(database, provider).stat(
        key,
        "/workspace/state.bin",
        deadline_at=deadline,
    )

    assert created.ready is True
    assert released.ready is False
    assert resumed.ready is True
    assert resumed.allocation_id == created.allocation_id
    assert resumed.allocation_epoch == created.allocation_epoch + 1
    assert FakeSandbox.create_calls == 1
    assert FakeSandbox.connect_calls == 1
    assert provider_id in FakeSandbox.instances
    assert data == b"persistent"

    await lifecycle.destroy(key, deadline_at=deadline)
    assert provider_id not in FakeSandbox.instances
    assert FakeSandbox.kill_calls == [provider_id]
    assert stat.sha256 == (
        "sha256:75b07bb3ffb3b8ad63e79b983fbef8fd0ee8e7292144b4e7d3b57bd682074087"
    )


async def test_rate_limit_reuses_same_allocation_token_without_hot_loop(
    database: StateDatabase,
) -> None:
    FakeSandbox.fail_rate_limit = True
    provider = adapter()
    lifecycle = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    with pytest.raises(Exception) as raised:
        await lifecycle.ensure(
            key,
            profile().ref,
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
    pending = await lifecycle.ensure(
        key,
        profile().ref,
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    assert getattr(raised.value, "code").value == "RATE_LIMITED"
    assert pending.ready is False
    assert pending.retry_after_ms is not None
    assert FakeSandbox.create_calls == 1


async def test_inventory_uses_exact_allocation_metadata(
    database: StateDatabase,
) -> None:
    provider, _lifecycle, _key, deadline, _handle = await provision(database)
    info = next(iter(FakeSandbox.infos.values()))

    matches = await provider.find_allocations(
        tuple(
            ProviderMetadataEntry(name, value) for name, value in info.metadata.items()
        ),
        deadline_at=deadline,
    )

    assert len(matches) == 1
    assert matches[0].provider_id == info.sandbox_id
    assert matches[0].workspace_storage is not None


async def test_native_process_files_and_python_are_provider_neutral(
    database: StateDatabase,
) -> None:
    provider, _lifecycle, key, deadline, _handle = await provision(database)
    process_service = ProcessExecutionService(database, provider)
    operation_id = uuid4()
    process, created = await process_service.start(
        key,
        StartProcessRequest(
            operation_id=operation_id,
            shell_command="printf ok",
            argv=None,
            cwd="/workspace",
            environment=(EnvironmentVariable("DYNAMIC_TOKEN", "secret"),),
            tty=None,
            output_limit_bytes=65536,
            deadline_at=deadline,
        ),
    )
    await asyncio.sleep(0)
    output = await process_service.read_output(
        key,
        operation_id,
        after_sequence=0,
        wait_seconds=0,
        deadline_at=deadline,
    )

    python = PythonSessionService(database, provider)
    session_id = uuid4()
    session, _ = await python.create(
        key,
        CreatePythonSessionRequest(
            session_id=session_id,
            cwd="/workspace",
            environment_keys=("DYNAMIC_TOKEN",),
            deadline_at=deadline,
        ),
    )
    result, _ = await python.execute(
        key,
        session_id,
        ExecutePythonRequest(
            operation_id=uuid4(),
            code="40 + 2",
            environment=(EnvironmentVariable("DYNAMIC_TOKEN", "fresh"),),
            output_limit_bytes=65536,
            deadline_at=deadline,
        ),
    )

    assert created is True
    assert process.state == ProcessState.RUNNING
    assert b"native-out\n" in b"".join(item.data for item in output.chunks)
    assert output.state == ProcessState.SUCCEEDED
    assert output.exit_code == 0
    assert session.environment_keys == ("DYNAMIC_TOKEN",)
    assert result.state == PythonExecutionState.SUCCEEDED
    assert result.result == "42"
    sandbox = next(iter(FakeSandbox.instances.values()))
    assert sandbox.last_python_env == {"DYNAMIC_TOKEN": "fresh"}
    assert "secret" not in repr(session)
    assert database.active_units_of_work == 0
    assert provider.workspace_storage_kind == StorageKind.SANDBOX_NATIVE
