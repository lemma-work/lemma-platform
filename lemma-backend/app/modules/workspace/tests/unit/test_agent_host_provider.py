"""The host provider: every operation as the op it becomes, every refusal mapped.

The transport is the seam: a fake that records each ``op`` and answers from a
script, standing where the Agent Host link client stands in production. What
the tests assert is the wire -- method names and params exactly as
docs/architecture/desktop-host-execution.md §4 spells them -- and the
``sandbox_runtime`` error each ``detail.kind`` becomes.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from sandbox_runtime.errors import (
    SandboxCapabilityUnsupported,
    SandboxOperationAmbiguous,
    SandboxPathConflict,
    SandboxPathNotFound,
    SandboxProcessNotFound,
    SandboxRejected,
    SandboxUnavailable,
)
from sandbox_runtime.protocol import (
    ByteRange,
    EnvironmentVariable,
    FileKind,
    ProcessOutputChannel,
    ProcessState,
    StartProcessRequest,
    TerminalSize,
)

from app.modules.workspace.domain.host_execution import (
    HostFolder,
    HostTarget,
    conversation_of_host_sandbox_slug,
    host_sandbox_id,
    host_sandbox_slug,
)
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.agent_host import (
    AgentHostSandboxProvider,
    HostOpRefused,
)
from app.modules.workspace.providers.agent_host_ops import (
    OP_MAX_DATA_BYTES,
    PYTHON_ON_HOST_SENTENCE,
)
from app.modules.workspace.providers.base import (
    ProviderCapability,
    ProviderCreateSpec,
    ProviderInstance,
    ProviderRejected,
)

ROOT = "/Users/owner/lemma/c/2026-09-25/abc12345"


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def _stat(path: str, *, size: int = 3, sha: str | None = None) -> dict:
    return {
        "path": path,
        "kind": "file",
        "size_bytes": size,
        "modified_at": "2026-09-25T10:00:00Z",
        "mode": 0o644,
        "sha256": sha,
    }


class FakeTransport:
    """Records every op; answers from ``answers[method]`` or raises a refusal."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, str, dict]] = []
        self.answers: dict[str, object] = {"workspace.open": {"root": ROOT}}
        self.refusals: dict[str, list[HostOpRefused]] = {}

    async def request(self, *, host_id, workspace, method, params, deadline_at):
        self.calls.append((host_id, workspace, method, params))
        queued = self.refusals.get(method)
        if queued:
            raise queued.pop(0)
        answer = self.answers.get(method, {})
        return answer(params) if callable(answer) else answer

    def methods(self) -> list[str]:
        return [call[2] for call in self.calls]

    def params(self, method: str) -> list[dict]:
        return [call[3] for call in self.calls if call[2] == method]


class FakeTargets:
    """Which host each sandbox's ops go to, as the run records would say."""

    def __init__(
        self,
        targets: dict[UUID, HostTarget] | None = None,
        folder: HostFolder | None = None,
    ) -> None:
        self.targets = targets or {}
        self._folder = folder

    async def target(self, sandbox_id: UUID) -> HostTarget | None:
        return self.targets.get(sandbox_id)

    async def folder(self, conversation_id: UUID) -> HostFolder | None:
        return self._folder


@pytest.fixture
def conversation_id() -> UUID:
    return uuid4()


@pytest.fixture
def sandbox_id(conversation_id) -> UUID:
    return host_sandbox_id(conversation_id)


@pytest.fixture
def host_id() -> UUID:
    return uuid4()


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


FOLDER = HostFolder(day="2026-09-25", slug="abc12345")


@pytest.fixture
def targets(sandbox_id, host_id, conversation_id) -> FakeTargets:
    return FakeTargets(
        {
            sandbox_id: HostTarget(
                host_id=host_id, conversation_id=conversation_id, root=ROOT
            )
        },
        FOLDER,
    )


@pytest.fixture
def provider(transport, targets) -> AgentHostSandboxProvider:
    return AgentHostSandboxProvider(transport, targets)


@pytest.fixture
def instance(sandbox_id) -> ProviderInstance:
    name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1)
    return ProviderInstance(provider_id=name, name=name, running=True)


# ----------------------------------------------------------------- lifecycle


def test_the_sandbox_row_names_its_conversation(conversation_id):
    """How a host sandbox's host is found without a table: row -> conversation."""
    slug = host_sandbox_slug(conversation_id)
    assert conversation_of_host_sandbox_slug(slug) == conversation_id
    assert conversation_of_host_sandbox_slug("default") is None
    assert conversation_of_host_sandbox_slug("host-nothex") is None


async def test_opening_sends_the_chosen_host_the_conversations_folder(
    provider, transport, sandbox_id, host_id, conversation_id
):
    chosen = uuid4()
    root = await provider.open_workspace(
        sandbox_id,
        host_id=chosen,
        conversation_id=conversation_id,
        folder=HostFolder(day="2026-09-25", slug="abc12345", root_hint="/Users/o/x"),
        deadline_at=_deadline(),
    )

    assert root == ROOT
    # The host selection chose, not whatever a lookup would say.
    assert transport.calls == [
        (
            chosen,
            sandbox_id,
            "workspace.open",
            {
                "root_hint": "/Users/o/x",
                "grants": [],
                "conversation_id": str(conversation_id),
                "date": "2026-09-25",
                "slug": "abc12345",
            },
        )
    ]


async def test_a_host_that_answers_no_folder_is_refused(
    provider, transport, sandbox_id, conversation_id
):
    transport.answers["workspace.open"] = {"root": "relative"}
    with pytest.raises(ProviderRejected, match="which folder"):
        await provider.open_workspace(
            sandbox_id,
            host_id=uuid4(),
            conversation_id=conversation_id,
            folder=FOLDER,
            deadline_at=_deadline(),
        )


async def test_opening_on_a_mac_that_is_not_connected_is_refused_in_words(
    provider, transport, sandbox_id, conversation_id
):
    transport.refusals["workspace.open"] = [
        HostOpRefused("host_offline", "This Mac is not connected, so ...")
    ]
    with pytest.raises(SandboxRejected, match="This Mac is not connected"):
        await provider.open_workspace(
            sandbox_id,
            host_id=uuid4(),
            conversation_id=conversation_id,
            folder=FOLDER,
            deadline_at=_deadline(),
        )


async def test_create_provisions_nothing_and_asks_nothing(
    provider, transport, instance, sandbox_id
):
    created = await provider.create(
        ProviderCreateSpec(
            sandbox_id=sandbox_id,
            kind=SandboxKind.WORKSPACE,
            epoch=1,
            name=instance.name,
            image="",
            profile_name="p",
            profile_digest="sha256:" + "0" * 64,
            deadline_at=_deadline(),
        )
    )

    assert created.running and created.name == instance.name
    assert transport.calls == []


async def test_release_and_destroy_close_the_workspace(provider, transport, instance):
    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    await provider.destroy(instance.name, deadline_at=_deadline())

    assert transport.methods() == ["workspace.close", "workspace.close"]


async def test_closing_on_a_mac_that_is_gone_is_already_done(
    provider, transport, instance
):
    transport.refusals["workspace.close"] = [
        HostOpRefused("host_offline", "gone"),
        HostOpRefused("workspace_not_open", "forgotten"),
    ]
    await provider.destroy(instance.name, deadline_at=_deadline())
    await provider.destroy(instance.name, deadline_at=_deadline())


async def test_inspect_asks_nothing_and_there_are_no_volumes_or_objects(
    provider, transport, instance, sandbox_id
):
    found = await provider.inspect(instance.name, deadline_at=_deadline())

    assert found is not None and found.running
    assert (
        await provider.find_volume(sandbox_id=sandbox_id, deadline_at=_deadline())
        is None
    )
    assert await provider.list_objects(deadline_at=_deadline()) == ()
    assert transport.calls == []


def test_capabilities_declare_secret_delivery_and_never_port_reach(provider):
    assert ProviderCapability.SECRET_DELIVERY in provider.capabilities
    assert ProviderCapability.PORT_REACH not in provider.capabilities


# ------------------------------------------------------------------ processes


async def test_start_process_sends_the_whole_request(provider, transport, instance):
    operation_id = uuid4()
    transport.answers["process.start"] = {"process_id": str(operation_id)}

    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=operation_id,
            shell_command="gh pr create --fill",
            argv=None,
            cwd=ROOT,
            environment=(EnvironmentVariable(name="LEMMA_TOKEN", value="t"),),
            tty=TerminalSize(cols=120, rows=40),
            output_limit_bytes=4096,
            deadline_at=_deadline(),
            initial_input=b"y\n",
        ),
        deadline_at=_deadline(),
    )

    assert process_id == str(operation_id)
    assert transport.params("process.start") == [
        {
            "operation_id": str(operation_id),
            "shell_command": "gh pr create --fill",
            "cwd": ROOT,
            "environment": [{"name": "LEMMA_TOKEN", "value": "t"}],
            "tty": {"rows": 40, "cols": 120},
            "output_limit_bytes": 4096,
            "initial_input": base64.b64encode(b"y\n").decode(),
        }
    ]


async def test_start_process_with_argv(provider, transport, instance):
    await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command=None,
            argv=("git", "status"),
            cwd=ROOT,
            environment=(),
            tty=None,
            output_limit_bytes=1024,
            deadline_at=_deadline(),
        ),
        deadline_at=_deadline(),
    )
    params = transport.params("process.start")[0]
    assert params["argv"] == ["git", "status"]
    assert "shell_command" not in params
    assert params["tty"] is None and params["initial_input"] is None


@pytest.mark.parametrize(
    ("state", "exit_code", "expected"),
    [
        ("running", None, ProcessState.RUNNING),
        ("exited", 0, ProcessState.SUCCEEDED),
        ("exited", 2, ProcessState.FAILED),
        ("killed", None, ProcessState.CANCELLED),
    ],
)
async def test_read_process_output_decodes_chunks_and_state(
    provider, transport, instance, state, exit_code, expected
):
    transport.answers["process.read"] = {
        "chunks": [
            {
                "sequence": 4,
                "stream": "stdout",
                "data": base64.b64encode(b"hi").decode(),
            },
            {
                "sequence": 5,
                "stream": "stderr",
                "data": base64.b64encode(b"!").decode(),
            },
        ],
        "next_sequence": 6,
        "truncated_before_sequence": 2,
        "state": state,
        "exit_code": exit_code,
    }

    snapshot = await provider.read_process_output(
        instance,
        process_id="p1",
        after_sequence=3,
        wait_seconds=1.5,
        deadline_at=_deadline(),
    )

    assert transport.params("process.read") == [
        {"process_id": "p1", "after_sequence": 3, "wait_ms": 1500}
    ]
    assert [(c.sequence, c.channel, c.data) for c in snapshot.chunks] == [
        (4, ProcessOutputChannel.STDOUT, b"hi"),
        (5, ProcessOutputChannel.STDERR, b"!"),
    ]
    assert snapshot.next_sequence == 6
    assert snapshot.truncated_before_sequence == 2
    assert snapshot.state is expected
    assert snapshot.exit_code == exit_code


async def test_input_resize_terminate_and_list(provider, transport, instance):
    transport.answers["process.list"] = {
        "processes": [
            {
                "process_id": "p1",
                "command": "npm run dev",
                "state": "running",
                "exit_code": None,
                "started_at": "2026-09-25T10:00:00Z",
            }
        ]
    }
    await provider.send_process_input(
        instance, process_id="p1", data=b"q", deadline_at=_deadline()
    )
    await provider.resize_process(
        instance,
        process_id="p1",
        size=TerminalSize(cols=80, rows=24),
        deadline_at=_deadline(),
    )
    await provider.terminate_process(
        instance, process_id="p1", grace_seconds=2.5, deadline_at=_deadline()
    )
    listed = await provider.list_processes(instance, deadline_at=_deadline())

    assert transport.params("process.input") == [
        {"process_id": "p1", "data": base64.b64encode(b"q").decode()}
    ]
    assert transport.params("process.resize") == [
        {"process_id": "p1", "rows": 24, "cols": 80}
    ]
    assert transport.params("process.terminate") == [
        {"process_id": "p1", "grace_ms": 2500}
    ]
    assert listed[0].process_id == "p1"
    assert listed[0].command == "npm run dev"
    assert listed[0].state is ProcessState.RUNNING
    assert listed[0].started_at is not None


# ---------------------------------------------------------------------- files


async def test_stat_list_mkdir_move_delete(provider, transport, instance):
    transport.answers["file.stat"] = _stat(f"{ROOT}/a.txt", sha="sha256:" + "a" * 64)
    transport.answers["file.list"] = {
        "entries": [_stat(f"{ROOT}/a.txt"), {**_stat(f"{ROOT}/d"), "kind": "directory"}]
    }
    transport.answers["file.delete"] = {"existed": True}

    stat = await provider.stat_file(
        instance, path=f"{ROOT}/a.txt", deadline_at=_deadline()
    )
    entries = await provider.list_files(instance, path=ROOT, deadline_at=_deadline())
    await provider.create_directory(instance, path=f"{ROOT}/n", deadline_at=_deadline())
    await provider.move_file(
        instance, source=f"{ROOT}/a", destination=f"{ROOT}/b", deadline_at=_deadline()
    )
    existed = await provider.delete_file(
        instance, path=f"{ROOT}/b", recursive=True, deadline_at=_deadline()
    )

    assert stat.kind is FileKind.FILE and stat.size_bytes == 3
    assert stat.sha256 == "sha256:" + "a" * 64
    assert [e.kind for e in entries] == [FileKind.FILE, FileKind.DIRECTORY]
    assert transport.params("file.mkdir") == [{"path": f"{ROOT}/n"}]
    assert transport.params("file.move") == [
        {"source": f"{ROOT}/a", "destination": f"{ROOT}/b"}
    ]
    assert transport.params("file.delete") == [{"path": f"{ROOT}/b", "recursive": True}]
    assert existed is True


async def test_a_large_read_moves_in_ranged_chunks_of_at_most_one_mebibyte(
    provider, transport, instance
):
    body = bytes(range(256)) * ((2 * OP_MAX_DATA_BYTES + 5000) // 256 + 1)
    body = body[: 2 * OP_MAX_DATA_BYTES + 5000]

    def read(params):
        piece = body[params["offset"] : params["offset"] + params["length"]]
        return {
            "data": base64.b64encode(piece).decode(),
            "eof": params["offset"] + len(piece) >= len(body),
        }

    transport.answers["file.read"] = read

    received = b"".join(
        [
            chunk
            async for chunk in provider.open_file(
                instance,
                path=f"{ROOT}/big",
                byte_range=ByteRange(offset=0, length=None),
                deadline_at=_deadline(),
            )
        ]
    )

    assert received == body
    reads = transport.params("file.read")
    assert [r["offset"] for r in reads] == [0, OP_MAX_DATA_BYTES, 2 * OP_MAX_DATA_BYTES]
    assert all(r["length"] <= OP_MAX_DATA_BYTES for r in reads)


async def test_a_ranged_read_asks_for_only_that_range(provider, transport, instance):
    transport.answers["file.read"] = {
        "data": base64.b64encode(b"xyz").decode(),
        "eof": False,
    }
    received = [
        chunk
        async for chunk in provider.open_file(
            instance,
            path=f"{ROOT}/f",
            byte_range=ByteRange(offset=10, length=3),
            deadline_at=_deadline(),
        )
    ]
    assert received == [b"xyz"]
    assert transport.params("file.read") == [
        {"path": f"{ROOT}/f", "offset": 10, "length": 3}
    ]


async def test_a_large_write_is_chunked_and_only_the_last_chunk_is_final(
    provider, transport, instance
):
    body = b"z" * (OP_MAX_DATA_BYTES * 2 + 17)
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    transport.answers["file.write"] = lambda params: (
        _stat(params["path"], size=len(body), sha=digest) if params["final"] else {}
    )

    async def stream():
        # Deliberately not aligned to the chunk size.
        for start in range(0, len(body), 700_000):
            yield body[start : start + 700_000]

    stat = await provider.write_file(
        instance,
        path=f"{ROOT}/out.bin",
        data=stream(),
        expected_sha256=digest,
        deadline_at=_deadline(),
    )

    writes = transport.params("file.write")
    assert [w["offset"] for w in writes] == [
        0,
        OP_MAX_DATA_BYTES,
        2 * OP_MAX_DATA_BYTES,
    ]
    assert [w["final"] for w in writes] == [False, False, True]
    assert len({w["upload_id"] for w in writes}) == 1
    assert all(len(base64.b64decode(w["data"])) <= OP_MAX_DATA_BYTES for w in writes)
    assert b"".join(base64.b64decode(w["data"]) for w in writes) == body
    assert "expected_sha256" not in writes[0]
    assert writes[-1]["expected_sha256"] == digest
    assert stat.size_bytes == len(body)


async def test_an_empty_write_is_one_final_chunk(provider, transport, instance):
    transport.answers["file.write"] = _stat(f"{ROOT}/empty", size=0)

    async def nothing():
        return
        yield b""

    await provider.write_file(
        instance,
        path=f"{ROOT}/empty",
        data=nothing(),
        expected_sha256=None,
        deadline_at=_deadline(),
    )
    writes = transport.params("file.write")
    assert len(writes) == 1 and writes[0]["final"] is True and writes[0]["data"] == ""


async def test_a_secret_is_delivered_without_a_shell(provider, transport, instance):
    await provider.deliver_secret(
        instance, path="/tmp/lemma/token", value=b"s3cret", deadline_at=_deadline()
    )
    assert transport.params("secret.deliver") == [
        {"path": "/tmp/lemma/token", "data": base64.b64encode(b"s3cret").decode()}
    ]
    assert "process.start" not in transport.methods()


# ---------------------------------------------------------------- not offered


async def test_python_sessions_say_to_use_python3_through_exec_command(
    provider, instance
):
    with pytest.raises(SandboxCapabilityUnsupported) as raised:
        await provider.execute_python(instance, None, None)
    assert str(raised.value) == PYTHON_ON_HOST_SENTENCE
    assert "python3" in str(raised.value) and "exec_command" in str(raised.value)
    with pytest.raises(SandboxCapabilityUnsupported):
        await provider.ensure_python_session(instance, None)


async def test_ports_are_not_reachable(provider, instance):
    with pytest.raises(SandboxCapabilityUnsupported):
        await provider.reach_port(instance, port=3000, deadline_at=_deadline())


# ------------------------------------------------------------------- failures


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("not_found", SandboxPathNotFound),
        ("already_exists", SandboxPathConflict),
        ("not_a_directory", SandboxPathConflict),
        ("is_a_directory", SandboxPathConflict),
        ("digest_mismatch", SandboxPathConflict),
        ("permission_denied", SandboxRejected),
        ("outside_workspace", SandboxRejected),
        ("too_large", SandboxRejected),
        ("process_not_found", SandboxProcessNotFound),
        ("exec_server_unavailable", SandboxUnavailable),
        ("timeout", SandboxOperationAmbiguous),
        ("link_lost", SandboxOperationAmbiguous),
        ("link_unavailable", SandboxUnavailable),
        ("host_offline", SandboxRejected),
        ("invalid_request", SandboxRejected),
        ("io_error", SandboxRejected),
        ("something_new", SandboxRejected),
    ],
)
async def test_every_failure_kind_maps_onto_a_sandbox_error(
    provider, transport, instance, kind, error
):
    transport.refusals["file.stat"] = [HostOpRefused(kind, f"host says {kind}")]

    with pytest.raises(error) as raised:
        await provider.stat_file(instance, path=f"{ROOT}/x", deadline_at=_deadline())

    assert str(raised.value)
    # A distinct type must not be swallowed by a broader one in the mapping.
    assert type(raised.value) is error


async def test_outside_workspace_is_explained(provider, transport, instance):
    transport.refusals["file.read"] = [HostOpRefused("outside_workspace", "denied")]
    with pytest.raises(SandboxRejected, match="outside this conversation's folder"):
        async for _ in provider.open_file(
            instance,
            path="/Users/owner/.ssh/id_rsa",
            byte_range=ByteRange(offset=0, length=None),
            deadline_at=_deadline(),
        ):
            pass


async def test_host_offline_reaches_the_agent_as_the_contract_sentence(
    provider, transport, instance
):
    transport.refusals["process.list"] = [
        HostOpRefused(
            "host_offline", "This Mac is not connected, so the command did not run."
        )
    ]
    with pytest.raises(SandboxRejected, match="This Mac is not connected"):
        await provider.list_processes(instance, deadline_at=_deadline())


async def test_a_host_that_forgot_the_workspace_is_reopened_once(
    provider, transport, instance, host_id, conversation_id
):
    transport.refusals["file.stat"] = [HostOpRefused("workspace_not_open", "?")]
    transport.answers["file.stat"] = _stat(f"{ROOT}/a")

    await provider.stat_file(instance, path=f"{ROOT}/a", deadline_at=_deadline())

    assert transport.methods() == ["file.stat", "workspace.open", "file.stat"]
    # Every op went to the recorded host, and the re-open hints the root the
    # run recorded, beside the folder inputs the first open sent.
    assert {call[0] for call in transport.calls} == {host_id}
    assert transport.params("workspace.open") == [
        {
            "root_hint": ROOT,
            "grants": [],
            "conversation_id": str(conversation_id),
            "date": "2026-09-25",
            "slug": "abc12345",
        }
    ]


async def test_a_reopen_before_any_run_recorded_a_root_hints_the_bound_folder(
    transport, instance, sandbox_id, host_id, conversation_id
):
    provider = AgentHostSandboxProvider(
        transport,
        FakeTargets(
            {sandbox_id: HostTarget(host_id=host_id, conversation_id=conversation_id)},
            HostFolder(day="2026-09-25", slug="abc12345", root_hint="/Users/o/x"),
        ),
    )
    transport.refusals["file.stat"] = [HostOpRefused("workspace_not_open", "?")]
    transport.answers["file.stat"] = _stat(f"{ROOT}/a")

    await provider.stat_file(instance, path=f"{ROOT}/a", deadline_at=_deadline())

    assert transport.params("workspace.open")[0]["root_hint"] == "/Users/o/x"


async def test_a_reopen_of_a_vanished_conversation_leaves_the_folder_to_the_mac(
    transport, instance, sandbox_id, host_id, conversation_id
):
    provider = AgentHostSandboxProvider(
        transport,
        FakeTargets(
            {sandbox_id: HostTarget(host_id=host_id, conversation_id=conversation_id)}
        ),
    )
    transport.refusals["file.stat"] = [HostOpRefused("workspace_not_open", "?")]
    transport.answers["file.stat"] = _stat(f"{ROOT}/a")

    await provider.stat_file(instance, path=f"{ROOT}/a", deadline_at=_deadline())

    assert transport.params("workspace.open") == [
        {"root_hint": None, "grants": [], "conversation_id": str(conversation_id)}
    ]


async def test_reopening_is_tried_once_only(provider, transport, instance):
    transport.refusals["file.stat"] = [
        HostOpRefused("workspace_not_open", "?"),
        HostOpRefused("workspace_not_open", "?"),
    ]
    with pytest.raises(SandboxUnavailable):
        await provider.stat_file(instance, path=f"{ROOT}/a", deadline_at=_deadline())
    assert transport.methods().count("workspace.open") == 1


async def test_an_op_for_a_sandbox_with_no_host_is_host_offline_not_the_vm(
    transport,
):
    provider = AgentHostSandboxProvider(transport, FakeTargets())
    name = naming.container_name(host_sandbox_id(uuid4()), SandboxKind.WORKSPACE, 1)
    with pytest.raises(SandboxRejected, match="This Mac is not connected"):
        await provider.stat_file(
            ProviderInstance(provider_id=name, name=name),
            path="/x",
            deadline_at=_deadline(),
        )
    assert transport.calls == []


async def test_closing_a_sandbox_with_no_host_is_already_done(transport):
    provider = AgentHostSandboxProvider(transport, FakeTargets())
    name = naming.container_name(host_sandbox_id(uuid4()), SandboxKind.WORKSPACE, 1)
    await provider.destroy(name, deadline_at=_deadline())
    assert transport.calls == []


async def test_a_read_never_asks_the_host_to_wait_past_thirty_seconds(
    provider, transport, instance
):
    transport.answers["process.read"] = {"state": "running", "next_sequence": 1}
    await provider.read_process_output(
        instance,
        process_id="p1",
        after_sequence=0,
        wait_seconds=45,
        deadline_at=_deadline(),
    )
    assert transport.params("process.read")[0]["wait_ms"] == 30_000
