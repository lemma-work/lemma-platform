"""A workspace sandbox in the real macOS guest, doing real work.

`test_lemma_local_real_bridge.py` proves everything between the provider and
the guest boundary, with a Python stub standing in for the guest itself. It
says so in its own docstring, and the gap is not theoretical: the stub once
agreed with the provider that `sandbox.list` returns a top-level `sandbox_id`
when the real guest nests it at `status.id`.

This closes that gap. Nothing here is stubbed — the provider drives the shipped
`lemma-runtime` bridge, which talks to the guest daemon inside the
Virtualization.framework VM Lemma Desktop boots, which starts a real container
from a real image, in which real shells and a real interpreter run.

Two properties a user notices:

* **A workspace does what it says.** Shells run, exit codes come back, the
  Python session remembers what the last call put in it, and the sandbox can
  reach the network. These are separate round trips through separate parts of
  the runtime protocol and there is no single "it works" to assert.
* **A workspace's files outlive its compute.** Releasing a sandbox stops the
  container and keeps the disk, so the next tool call is slower but nothing is
  lost. That contract spans the provider, the guest, and the volume the guest
  mounts, and no test below the guest boundary can observe it.

## Running it

Needs a Lemma Desktop install whose runtime is up — the VM has to be booted,
because these tests do not boot it:

    open -a Lemma            # let Local finish starting

    export LEMMA_GUEST_CONTROL_SOCKET=~/Library/Application\\ Support/Lemma/\\
    locald/run/guest.sock
    export LEMMA_GUEST_CAPABILITY_FILE=<the control share>/guest.capability
    export LEMMA_LOCAL_REAL_GUEST=1
    export WORKSPACE_IMAGE=<a digest-pinned workspace image the guest can pull>

    cd lemma-backend && uv run pytest -m local_guest -q

`docs/local-runtime-vm.md` covers driving a VM by hand, including where those
two paths are and how to stand one up outside Desktop.

Opt-in on purpose. It boots real containers in a VM with a 24 GiB data disk and
pulls images over the network, so it belongs with the release-qualification
suites rather than in the run that gates a PR.
"""

from __future__ import annotations

import os
import shutil
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from sandbox_runtime.protocol import (
    ByteRange,
    CreatePythonSessionRequest,
    EnvironmentVariable,
    ExecutePythonRequest,
    StartProcessRequest,
)

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import ProviderCreateSpec, ProviderGone
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.providers.lemma_local import (
    LemmaLocalProviderConfig,
    LemmaLocalSandboxProvider,
    LocalBridgeError,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.local_guest,
    pytest.mark.asyncio,
]

# Long enough that a cold image pull inside the guest is not read as a hang.
GUEST_DEADLINE_SECONDS = 600
OUTPUT_LIMIT_BYTES = 64 * 1024


def _deadline(seconds: int = GUEST_DEADLINE_SECONDS) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _bridge_binary() -> Path | None:
    override = os.getenv("LEMMA_MANAGED_RUNTIME_CLI")
    if override and Path(override).is_file():
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        built = parent / "desktop/target/release/lemma-runtime"
        if built.is_file():
            return built
    found = shutil.which("lemma-runtime")
    return Path(found) if found else None


def _requirements() -> list[str]:
    """Everything missing that this suite cannot supply for itself.

    Reported together rather than one skip at a time: standing this up is a
    handful of steps, and finding out about them one run apiece is the kind of
    thing that stops people running it at all.
    """

    missing: list[str] = []
    if os.getenv("LEMMA_LOCAL_REAL_GUEST") != "1":
        missing.append("LEMMA_LOCAL_REAL_GUEST=1 to opt in")
    for name in ("LEMMA_GUEST_CONTROL_SOCKET", "LEMMA_GUEST_CAPABILITY_FILE"):
        value = os.getenv(name)
        if not value:
            missing.append(f"{name} is unset")
        elif not Path(value).exists():
            missing.append(f"{name} points at {value!r}, which does not exist")
    if not os.getenv("WORKSPACE_IMAGE"):
        missing.append("WORKSPACE_IMAGE (digest-pinned) so the guest knows what to run")
    if _bridge_binary() is None:
        missing.append(
            "the bridge: cargo build --release "
            "--manifest-path desktop/Cargo.toml -p lemma-runtime"
        )
    return missing


# What a guest that is unwell answers with. Anything else during cleanup is a
# bug in the test, and should surface as itself rather than as a leak.
_CLEANUP_FAILURES = (LocalBridgeError, ProviderGone)


async def _remove(provider, sandbox_id, *names: str) -> None:
    """Take a sandbox and its disk out of the guest, whatever state they are in.

    Every step runs even when an earlier one fails. A `destroy` that raises must
    not also strand the volume — that is guest disk keyed to an id nothing will
    ask for again — and the first failure is re-raised afterwards so a guest
    that cannot clean up still fails the run rather than quietly accumulating.
    """
    failures: list[BaseException] = []
    for name in names:
        try:
            await provider.destroy(name, deadline_at=_deadline(120))
        except _CLEANUP_FAILURES as error:
            failures.append(error)
    try:
        await provider.purge_storage(
            LemmaLocalSandboxProvider._guest_id(sandbox_id, SandboxKind.WORKSPACE),
            deadline_at=_deadline(120),
        )
    except _CLEANUP_FAILURES as error:
        failures.append(error)
    if failures:
        raise failures[0]


def _spec(sandbox_id, *, name: str, volume_name: str) -> ProviderCreateSpec:
    return ProviderCreateSpec(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=1,
        name=name,
        image=os.environ["WORKSPACE_IMAGE"],
        profile_name="workspace",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=_deadline(),
        volume_name=volume_name,
    )


@pytest_asyncio.fixture(scope="module")
async def guest_provider():
    missing = _requirements()
    if missing:
        pytest.skip("real guest unavailable — " + "; ".join(missing))

    provider = LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(executable=str(_bridge_binary())),
        # Signs the token the in-sandbox runtime accepts. Any stable key works
        # here: nothing outside this test validates it.
        RuntimeCredentialSigner(key=b"real-guest-test-key-padded-to-32"),
    )
    try:
        yield provider
    finally:
        await provider.close()


class Workspace:
    """One live sandbox, and the names needed to address it again."""

    def __init__(self, provider, instance, sandbox_id, name, volume_name):
        self.provider = provider
        self.instance = instance
        self.sandbox_id = sandbox_id
        self.name = name
        self.volume_name = volume_name

    async def shell(self, command: str, *, wait_seconds: float = 30) -> tuple[bytes, int | None]:
        """Run a command and read what it printed, to completion."""
        process_id = await self.provider.start_process(
            self.instance,
            StartProcessRequest(
                operation_id=uuid4(),
                shell_command=command,
                argv=None,
                cwd="/workspace",
                environment=(EnvironmentVariable(name="LEMMA_TEST", value="1"),),
                tty=None,
                output_limit_bytes=OUTPUT_LIMIT_BYTES,
                deadline_at=_deadline(),
                initial_input=None,
            ),
            deadline_at=_deadline(),
        )
        output = b""
        sequence = 0
        exit_code: int | None = None
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            snapshot = await self.provider.read_process_output(
                self.instance,
                process_id=process_id,
                after_sequence=sequence,
                wait_seconds=5,
                deadline_at=_deadline(),
            )
            for chunk in snapshot.chunks:
                output += chunk.data
                sequence = max(sequence, chunk.sequence)
            exit_code = snapshot.exit_code
            if snapshot.exit_code is not None:
                break
        return output, exit_code


@pytest_asyncio.fixture(scope="module")
async def workspace(guest_provider):
    """One workspace sandbox, destroyed with its volume however the tests end.

    Module-scoped because every execution test below wants the same live
    container and creating one costs a container start each time. Cleanup is
    unconditional because a leak here is not a stray row — it is a container
    and a disk inside a VM that outlives the test session.

    **Shared, so nothing here may assume a clean disk.** The tests that use it
    write under distinct, uuid-suffixed names for that reason. A test that needs
    an empty workspace — or that stops, releases or destroys the container —
    builds its own, the way the lifecycle tests at the bottom of this file do.
    """

    sandbox_id = uuid4()
    name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1)
    volume_name = naming.volume_name(sandbox_id, 1)

    instance = await guest_provider.create(
        _spec(sandbox_id, name=name, volume_name=volume_name)
    )
    # `wait_ready` inside the try, not before it. It raises under guest memory
    # pressure and on a slow container start, and the container `create` has
    # already built holds its full 2 GiB reservation until something removes it.
    # One leak left 325 MiB free and every later test in this file failed with
    # `resource_capacity` — which reads like a code regression and is not one.
    try:
        await guest_provider.wait_ready(
            instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
        )
        yield Workspace(guest_provider, instance, sandbox_id, name, volume_name)
    finally:
        await _remove(guest_provider, sandbox_id, name)


# ---------------------------------------------------------------------------
# The sandbox exists and is reachable
# ---------------------------------------------------------------------------


async def test_a_workspace_sandbox_reaches_ready_in_the_real_guest(workspace) -> None:
    """The whole chain, once: provider to bridge to guest to a live container.

    Everything below asserts on a sandbox that got here, so when this fails the
    rest is noise.
    """

    found = await workspace.provider.inspect(workspace.name, deadline_at=_deadline(120))

    assert found is not None, "a sandbox that just went ready must be findable"
    assert found.running, "a ready sandbox has to be running or no tool call can land"


async def test_the_runtime_port_resolves_to_something_the_backend_can_call(
    workspace,
) -> None:
    """Every tool call goes to this URL. Without it the sandbox is unreachable."""
    url = await workspace.provider.port_base_url(
        workspace.instance, port=8080, deadline_at=_deadline(120)
    )

    assert url.startswith("http://"), url


# ---------------------------------------------------------------------------
# Real execution
# ---------------------------------------------------------------------------


async def test_a_shell_command_runs_and_returns_its_output(workspace) -> None:
    marker = f"shell-{uuid4().hex[:8]}"

    output, exit_code = await workspace.shell(f"echo {marker}")

    assert marker.encode() in output, output
    assert exit_code == 0


async def test_a_failing_shell_command_reports_its_exit_code(workspace) -> None:
    """A tool that cannot tell success from failure is worse than no tool."""
    output, exit_code = await workspace.shell("echo to-stderr >&2; exit 3")

    assert exit_code == 3, (output, exit_code)
    assert b"to-stderr" in output, output


async def test_the_shell_writes_files_the_shell_can_read_back(workspace) -> None:
    marker = f"file-{uuid4().hex[:8]}"

    written, _ = await workspace.shell(
        f"mkdir -p artifacts && printf '{marker}' > artifacts/proof.txt && "
        "cat artifacts/proof.txt"
    )

    assert marker.encode() in written, written


async def test_a_long_running_process_can_be_listed_and_terminated(workspace) -> None:
    """Nothing else can stop a runaway process a user started."""
    process_id = await workspace.provider.start_process(
        workspace.instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command="sleep 120",
            argv=None,
            cwd="/workspace",
            environment=(),
            tty=None,
            output_limit_bytes=OUTPUT_LIMIT_BYTES,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )

    running = await workspace.provider.list_processes(
        workspace.instance, deadline_at=_deadline(60)
    )
    assert any(descriptor.process_id == process_id for descriptor in running), running

    await workspace.provider.terminate_process(
        workspace.instance,
        process_id=process_id,
        grace_seconds=2,
        deadline_at=_deadline(60),
    )

    snapshot = await workspace.provider.read_process_output(
        workspace.instance,
        process_id=process_id,
        after_sequence=0,
        wait_seconds=15,
        deadline_at=_deadline(60),
    )
    assert snapshot.exit_code is not None, "a terminated process has to end"


async def test_python_keeps_state_across_executions(workspace) -> None:
    """One Python session across tool calls is the whole point of having one.

    Two `exec` calls that cannot see each other's variables would make every
    notebook-shaped task impossible, and it fails invisibly: the second call
    raises NameError rather than reporting a broken session.
    """
    session_id = uuid4()
    await workspace.provider.ensure_python_session(
        workspace.instance,
        CreatePythonSessionRequest(
            session_id=session_id,
            cwd="/workspace",
            environment_keys=(),
            deadline_at=_deadline(120),
        ),
    )
    session = _session_ref(session_id)

    first = await workspace.provider.execute_python(
        workspace.instance,
        session,
        ExecutePythonRequest(
            operation_id=uuid4(),
            code="value = 6 * 7",
            environment=(),
            output_limit_bytes=OUTPUT_LIMIT_BYTES,
            deadline_at=_deadline(120),
        ),
    )
    assert "Traceback" not in first.stderr, first.stderr

    second = await workspace.provider.execute_python(
        workspace.instance,
        session,
        ExecutePythonRequest(
            operation_id=uuid4(),
            code="print(value)",
            environment=(),
            output_limit_bytes=OUTPUT_LIMIT_BYTES,
            deadline_at=_deadline(120),
        ),
    )
    assert "42" in second.stdout, (second.stdout, second.stderr)


async def test_python_reports_user_code_failure_as_a_result(workspace) -> None:
    """A raised exception is an answer, not a transport failure."""
    session_id = uuid4()
    await workspace.provider.ensure_python_session(
        workspace.instance,
        CreatePythonSessionRequest(
            session_id=session_id,
            cwd="/workspace",
            environment_keys=(),
            deadline_at=_deadline(120),
        ),
    )

    result = await workspace.provider.execute_python(
        workspace.instance,
        _session_ref(session_id),
        ExecutePythonRequest(
            operation_id=uuid4(),
            code="raise RuntimeError('intentional guest python failure')",
            environment=(),
            output_limit_bytes=OUTPUT_LIMIT_BYTES,
            deadline_at=_deadline(120),
        ),
    )

    assert "intentional guest python failure" in (
        (result.error_message or "") + (result.traceback or "") + result.stderr
    ), result


async def test_python_and_the_shell_share_one_filesystem(workspace) -> None:
    """The two halves of a task have to meet somewhere, and it is the disk."""
    marker = f"shared-{uuid4().hex[:8]}"
    await workspace.shell(f"printf '{marker}' > /workspace/shared.txt")

    session_id = uuid4()
    await workspace.provider.ensure_python_session(
        workspace.instance,
        CreatePythonSessionRequest(
            session_id=session_id,
            cwd="/workspace",
            environment_keys=(),
            deadline_at=_deadline(120),
        ),
    )
    result = await workspace.provider.execute_python(
        workspace.instance,
        _session_ref(session_id),
        ExecutePythonRequest(
            operation_id=uuid4(),
            code="print(open('/workspace/shared.txt').read())",
            environment=(),
            output_limit_bytes=OUTPUT_LIMIT_BYTES,
            deadline_at=_deadline(120),
        ),
    )

    assert marker in result.stdout, (result.stdout, result.stderr)


async def test_the_sandbox_can_fetch_over_the_network(workspace) -> None:
    """A workspace that cannot reach the web cannot do most of what it is for.

    Claims only that the sandbox has the reach this machine has: skipped rather
    than failed when the host itself is offline, because that is a fact about
    the room, not about the guest.
    """
    if not _host_can_reach("example.com", 443):
        pytest.skip("this machine cannot reach the public internet")

    output, exit_code = await workspace.shell(
        "python3 -c \"import urllib.request;"
        "print(urllib.request.urlopen('https://example.com', timeout=30).status)\"",
        wait_seconds=60,
    )

    assert exit_code == 0, output
    assert b"200" in output, output


async def test_dns_resolves_inside_the_sandbox(workspace) -> None:
    """Named separately from the fetch: a broken resolver and a blocked egress
    look identical from one failing request, and they are fixed in different
    places."""
    if not _host_can_reach("example.com", 443):
        pytest.skip("this machine cannot reach the public internet")

    output, exit_code = await workspace.shell(
        "python3 -c \"import socket; print(socket.gethostbyname('example.com'))\"",
        wait_seconds=60,
    )

    assert exit_code == 0, output


async def test_the_sandbox_clock_agrees_with_this_mac(workspace) -> None:
    """A guest running in the past breaks far more than it looks like it should.

    The auth service runs in this same guest, so a clock that has drifted mints
    access tokens that are expired on arrival — which presents as a workspace
    that is signed in and unable to do anything. Anything else the guest stamps
    with a time is wrong for as long as the drift lasts.
    """
    output, exit_code = await workspace.shell("date +%s")

    assert exit_code == 0, output
    guest_epoch = int(output.decode().strip().splitlines()[-1])
    assert abs(guest_epoch - time.time()) < 120, (
        f"the guest clock is {guest_epoch - time.time():.0f}s from this Mac's; "
        "locald's clock keeper should hold it within seconds"
    )


# ---------------------------------------------------------------------------
# Cleanup, which is the part that costs a live install when it is wrong
# ---------------------------------------------------------------------------


async def test_cleanup_purges_the_disk_even_when_the_container_will_not_go() -> None:
    """A `destroy` that fails must not also strand the volume.

    The two halves used to run in sequence with nothing between them, so the
    first failure took the second with it — leaving guest disk keyed to an id
    nothing will ask for again, and no explanation in the next run. Needs no
    guest: the point is the ordering, not the engine.
    """
    purged: list[str] = []

    class RefusesToDestroy:
        async def destroy(self, name, *, deadline_at):
            raise LocalBridgeError(f"the engine will not remove {name}")

        async def purge_storage(self, guest_id, *, deadline_at):
            purged.append(guest_id)

    sandbox_id = uuid4()
    with pytest.raises(LocalBridgeError):
        await _remove(RefusesToDestroy(), sandbox_id, "w-whatever")

    assert purged == [
        LemmaLocalSandboxProvider._guest_id(sandbox_id, SandboxKind.WORKSPACE)
    ], "the disk was stranded because the container would not go"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_a_released_workspace_reports_stopped_rather_than_broken(
    guest_provider,
) -> None:
    """Release is the ordinary resting state of an idle workspace, not a fault.

    The guest derives this from the container's inspect output, which does not
    always carry a status string — and reading a missing one as ERROR made the
    state every workspace spends most of its life in look like a broken one.
    """
    sandbox_id = uuid4()
    name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1)
    volume_name = naming.volume_name(sandbox_id, 1)
    instance = await guest_provider.create(
        _spec(sandbox_id, name=name, volume_name=volume_name)
    )
    try:
        await guest_provider.wait_ready(
            instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
        )
        await guest_provider.release(
            instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline(120)
        )

        found = await guest_provider.inspect(name, deadline_at=_deadline(120))

        assert found is not None, "release keeps the container; it does not remove it"
        assert not found.running
    finally:
        await _remove(guest_provider, sandbox_id, name)


async def test_files_written_to_a_workspace_outlive_its_container(
    guest_provider,
) -> None:
    """Release stops compute and keeps the disk — the reason it is safe to sweep.

    An idle workspace is released automatically after
    WORKSPACE_IDLE_RELEASE_SECONDS. If the volume did not survive that, the
    sweep would silently destroy work, and the failure would surface hours
    later as "my files are gone" rather than as anything a log would show.
    """
    sandbox_id = uuid4()
    name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1)
    volume_name = naming.volume_name(sandbox_id, 1)
    marker = f"persisted-{uuid4().hex}"
    path = "/workspace/persistence-check.txt"

    instance = await guest_provider.create(
        _spec(sandbox_id, name=name, volume_name=volume_name)
    )
    try:
        await guest_provider.wait_ready(
            instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
        )

        async def chunks():
            yield marker.encode()

        written = await guest_provider.write_file(
            instance,
            path=path,
            data=chunks(),
            expected_sha256=None,
            deadline_at=_deadline(120),
        )
        assert written.path == path

        # Release, not destroy: this is the sweep's move.
        await guest_provider.release(
            instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline(120)
        )

        # The same sandbox id and volume, a new container: what a tool call
        # after an idle release actually does.
        revived = await guest_provider.create(
            _spec(sandbox_id, name=name, volume_name=volume_name)
        )
        await guest_provider.wait_ready(
            revived, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
        )

        restored = b"".join(
            [
                chunk
                async for chunk in guest_provider.open_file(
                    revived,
                    path=path,
                    byte_range=ByteRange(offset=0, length=None),
                    deadline_at=_deadline(120),
                )
            ]
        )
        assert restored == marker.encode(), (
            "a released workspace must come back with its files; losing them "
            "here means the idle sweep destroys user work"
        )
    finally:
        await _remove(guest_provider, sandbox_id, name)


async def test_a_rebuilt_workspace_keeps_its_files_across_an_epoch_change(
    guest_provider,
) -> None:
    """What the service actually does to a stopped desktop workspace.

    It cannot resume one, so it provisions again — and provisioning always
    moves the epoch, which changes the container name. The guest keys a
    workspace's disk on the sandbox id *without* the epoch precisely so that
    this stays the same workspace; if it did not, the fix for "a stopped
    workspace never comes back" would come back with an empty disk instead.
    """
    sandbox_id = uuid4()
    first_name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1)
    second_name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 2)
    marker = f"rebuilt-{uuid4().hex}"

    instance = await guest_provider.create(
        _spec(sandbox_id, name=first_name, volume_name=naming.volume_name(sandbox_id, 1))
    )
    try:
        await guest_provider.wait_ready(
            instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
        )

        async def chunks():
            yield marker.encode()

        await guest_provider.write_file(
            instance,
            path="/workspace/rebuild-check.txt",
            data=chunks(),
            expected_sha256=None,
            deadline_at=_deadline(120),
        )
        await guest_provider.release(
            instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline(120)
        )

        rebuilt = await guest_provider.create(
            _spec(
                sandbox_id,
                name=second_name,
                volume_name=naming.volume_name(sandbox_id, 2),
            )
        )
        await guest_provider.wait_ready(
            rebuilt, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
        )

        restored = b"".join(
            [
                chunk
                async for chunk in guest_provider.open_file(
                    rebuilt,
                    path="/workspace/rebuild-check.txt",
                    byte_range=ByteRange(offset=0, length=None),
                    deadline_at=_deadline(120),
                )
            ]
        )
        assert restored == marker.encode(), (
            "a rebuilt workspace came back on a different disk; the epoch is "
            "leaking into the guest's storage key"
        )
    finally:
        await _remove(guest_provider, sandbox_id, second_name, first_name)


async def test_a_workspace_volume_is_not_shared_between_sandboxes(
    guest_provider, workspace
) -> None:
    """One sandbox's files must not appear in another's.

    Volume names are derived, so a derivation that collapses two ids to one
    name would hand a user someone else's workspace — and it would look like
    ordinary reuse, not like a leak.
    """
    secret = f"private-{uuid4().hex}"
    await workspace.shell(f"printf '{secret}' > /workspace/private.txt")

    other_id = uuid4()
    other_name = naming.container_name(other_id, SandboxKind.WORKSPACE, 1)
    other = await guest_provider.create(
        _spec(other_id, name=other_name, volume_name=naming.volume_name(other_id, 1))
    )
    try:
        await guest_provider.wait_ready(
            other, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
        )
        process_id = await guest_provider.start_process(
            other,
            StartProcessRequest(
                operation_id=uuid4(),
                shell_command="cat /workspace/private.txt || echo absent",
                argv=None,
                cwd="/workspace",
                environment=(),
                tty=None,
                output_limit_bytes=OUTPUT_LIMIT_BYTES,
                deadline_at=_deadline(),
                initial_input=None,
            ),
            deadline_at=_deadline(),
        )
        snapshot = await guest_provider.read_process_output(
            other,
            process_id=process_id,
            after_sequence=0,
            wait_seconds=30,
            deadline_at=_deadline(60),
        )
        seen = b"".join(chunk.data for chunk in snapshot.chunks)

        assert secret.encode() not in seen, "one workspace can read another's files"
    finally:
        await _remove(guest_provider, other_id, other_name)


def _host_can_reach(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


def _session_ref(session_id):
    """The SDK-neutral session reference the provider only reads an id from."""
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Ref:
        session_id: object

    return _Ref(session_id=session_id)
