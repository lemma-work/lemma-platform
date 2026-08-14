from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from sandbox_runtime.protocol import (
    ByteRange,
    EnvironmentVariable,
    StartProcessRequest,
    TerminalSize,
)

from sandbox_runtime.errors import (
    SandboxPathNotFound,
    SandboxUnavailable,
)
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    ProviderCreateSpec,
    ProviderGone,
    ProviderRejected,
)
from app.modules.workspace.providers.e2b import (
    E2BProviderConfig,
    E2BSandboxProvider,
)
from app.modules.workspace.providers.e2b_common import (
    META_EPOCH,
    META_PROFILE_DIGEST,
    META_SANDBOX_ID,
)
from app.modules.workspace.testing.fake_e2b import (
    AuthenticationException,
    FakeE2B,
    FakeSandboxSdk,
    NotFoundException,
    RateLimitException,
)

pytestmark = pytest.mark.asyncio


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


@pytest.fixture
def world() -> FakeE2B:
    return FakeE2B()


@pytest.fixture
def provider(world: FakeE2B, monkeypatch) -> E2BSandboxProvider:
    instance = E2BSandboxProvider(
        E2BProviderConfig(
            api_key="test-key",
            workspace_template="lemma-workspace",
            function_template="lemma-function",
        )
    )
    # Only the SDK is substituted. The query type comes through the SDK itself,
    # so this one patch is enough and the real e2b package is never imported.
    monkeypatch.setattr(
        type(instance), "_sdk", property(lambda self: world.sandbox_class())
    )
    return instance


def _spec(sandbox_id, *, epoch: int = 1, **overrides) -> ProviderCreateSpec:
    defaults = dict(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=epoch,
        name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, epoch),
        image="",
        profile_name="workspace",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=_deadline(),
    )
    defaults.update(overrides)
    return ProviderCreateSpec(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Identity, which E2B carries in metadata rather than in a name
# ---------------------------------------------------------------------------


async def test_a_sandbox_is_labelled_with_the_identity_it_belongs_to(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    sandbox_id = uuid4()
    await provider.create(_spec(sandbox_id, epoch=3))

    metadata = world.created[0]["metadata"]
    assert metadata[META_SANDBOX_ID] == str(sandbox_id)
    assert metadata[META_EPOCH] == "3"


async def test_creating_twice_yields_one_sandbox(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """Same property as the deterministic container name on Docker, reached by
    a metadata query instead: a retry finds what a lost response left behind."""
    sandbox_id = uuid4()

    first = await provider.create(_spec(sandbox_id))
    second = await provider.create(_spec(sandbox_id))

    assert first.provider_id == second.provider_id
    assert len(world.created) == 1


async def test_a_new_epoch_adopts_the_same_sandbox(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """Deliberately unlike Docker, and this is the important difference.

    On Docker a new epoch means a new container while the volume persists. Here
    the sandbox *is* the disk, so making a second one for a new epoch would
    leave the user's files in the first. Identity alone decides; the fence is
    the E2B sandbox id, which only changes when the sandbox really is new.
    """
    sandbox_id = uuid4()
    first = await provider.create(_spec(sandbox_id, epoch=1))
    second = await provider.create(
        _spec(
            sandbox_id,
            epoch=2,
            name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 2),
        )
    )

    assert first.provider_id == second.provider_id
    assert len(world.created) == 1
    assert first.storage_adopted is False, "the first provision made the disk"
    assert second.storage_adopted is True, "the second adopted it"


async def test_another_sandboxes_metadata_is_never_matched(
    provider: E2BSandboxProvider,
) -> None:
    await provider.create(_spec(uuid4()))
    assert (
        await provider.inspect(
            naming.container_name(uuid4(), SandboxKind.WORKSPACE, 1),
            deadline_at=_deadline(),
        )
        is None
    )


async def test_a_foreign_name_is_not_looked_up_at_all(
    provider: E2BSandboxProvider,
) -> None:
    assert await provider.inspect("postgres", deadline_at=_deadline()) is None


# ---------------------------------------------------------------------------
# Suspend, which E2B supports natively
# ---------------------------------------------------------------------------


async def test_release_pauses_rather_than_destroying(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """Pausing keeps the filesystem, so a released workspace resumes with the
    user's files intact instead of being rebuilt."""
    instance = await provider.create(_spec(uuid4()))

    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    assert world.paused == [instance.provider_id]
    assert world.killed == []
    assert instance.provider_id in world.sandboxes


async def test_a_paused_sandbox_is_found_and_resumed(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    sandbox_id = uuid4()
    instance = await provider.create(_spec(sandbox_id))
    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    found = await provider.inspect(
        naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )
    assert found is not None and found.provider_id == instance.provider_id

    await provider.wait_ready(
        found, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    assert world.sandboxes[instance.provider_id].state == "running"
    # Resuming must not create a second sandbox.
    assert len(world.created) == 1


async def test_destroying_something_already_gone_is_success(
    provider: E2BSandboxProvider,
) -> None:
    await provider.destroy(
        naming.container_name(uuid4(), SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def test_storage_is_the_sandbox_not_a_volume(
    provider: E2BSandboxProvider,
) -> None:
    """Production holds zero volumes: every workspace's files live inside a
    paused sandbox. Reporting a volume would send the service looking for a
    disk that does not exist, and creating one would leave the real files
    behind."""
    from app.modules.workspace.providers.base import ProviderStorageKind

    assert provider.storage_kind is ProviderStorageKind.SANDBOX_NATIVE
    assert (
        await provider.find_volume(sandbox_id=uuid4(), deadline_at=_deadline())
        is None
    )
    with pytest.raises(ProviderRejected):
        await provider.ensure_volume(
            sandbox_id=uuid4(), name="lemma-vol-x-1", deadline_at=_deadline()
        )


async def test_a_sandbox_is_made_from_the_template_not_from_an_image(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """On E2B the image settings are inert, and operators need to know it.

    `WORKSPACE_IMAGE` and `FUNCTION_IMAGE` describe an OCI artifact, which is
    what the Docker and lemma_local providers pull. E2B is made from a
    template instead, so a deployment on E2B that carefully pins an image is
    pinning nothing. Passing a deliberately wrong image here proves the
    provider never consults it.
    """
    sandbox_id = uuid4()

    await provider.create(_spec(sandbox_id, image="wrong-registry/nope@sha256:bad"))

    assert world.created[0]["template"] == "lemma-workspace"


async def test_a_function_sandbox_uses_the_function_template(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    await provider.create(_spec(uuid4(), kind=SandboxKind.FUNCTION))

    assert world.created[0]["template"] == "lemma-function"


async def test_a_sandbox_from_the_same_template_build_is_adopted(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """The fence must not cost E2B its whole reason for adopting: the paused
    sandbox holding the user's files has to come back."""
    from app.modules.workspace.testing.fake_e2b import FakeSandboxInfo

    sandbox_id = uuid4()
    spec = _spec(sandbox_id)
    world.sandboxes["paused-with-files"] = FakeSandboxInfo(
        sandbox_id="paused-with-files",
        state="paused",
        metadata={
            META_SANDBOX_ID: str(sandbox_id),
            META_PROFILE_DIGEST: spec.profile_digest,
        },
    )

    instance = await provider.create(spec)

    assert instance.provider_id == "paused-with-files"
    assert instance.storage_adopted is True
    assert world.created == [], "a second sandbox would strand the real one"
    assert world.killed == []


async def test_a_sandbox_from_an_older_template_build_is_destroyed(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """Here the sandbox is the disk, so there is no replacing the compute and
    keeping the files. Resuming it would leave the workspace on the old
    template, speaking a protocol the backend no longer speaks; the caller is
    told the disk is new through `storage_adopted`."""
    from app.modules.workspace.testing.fake_e2b import FakeSandboxInfo

    sandbox_id = uuid4()
    world.sandboxes["older-build"] = FakeSandboxInfo(
        sandbox_id="older-build",
        state="paused",
        metadata={
            META_SANDBOX_ID: str(sandbox_id),
            META_PROFILE_DIGEST: "sha256:" + "b" * 64,
        },
    )

    instance = await provider.create(_spec(sandbox_id))

    assert world.killed == ["older-build"]
    assert instance.provider_id != "older-build"
    assert instance.storage_adopted is False
    assert len(world.created) == 1


async def test_a_running_match_is_preferred_over_a_paused_duplicate(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """An earlier failure can leave two sandboxes for one identity; the one
    actually serving must not be shadowed by the stale one."""
    from app.modules.workspace.testing.fake_e2b import FakeSandboxInfo

    sandbox_id = uuid4()
    spec = _spec(sandbox_id)
    for name, state in (("stale", "paused"), ("live", "running")):
        world.sandboxes[name] = FakeSandboxInfo(
            sandbox_id=name,
            state=state,
            # Both carry the current profile digest, so the reuse fence is
            # satisfied and this exercises the ordering rule on its own.
            metadata={
                META_SANDBOX_ID: str(sandbox_id),
                META_PROFILE_DIGEST: spec.profile_digest,
            },
        )

    instance = await provider.create(spec)
    assert instance.provider_id == "live"


# ---------------------------------------------------------------------------
# Processes: streaming callbacks become a resumable cursor
# ---------------------------------------------------------------------------


async def test_streamed_output_becomes_a_readable_cursor(
    provider: E2BSandboxProvider, monkeypatch
) -> None:
    """The core adaptation. E2B pushes output; every caller above pulls it."""
    from app.modules.workspace.testing.fake_output_buffer import InMemoryOutputBuffer

    buffer = InMemoryOutputBuffer()
    monkeypatch.setattr(provider, "_output", buffer)
    monkeypatch.setattr(provider, "_remember_pid", buffer.remember_pid)
    monkeypatch.setattr(provider, "_recall_pid", buffer.recall_pid)

    instance = await provider.create(_spec(uuid4()))
    operation_id = uuid4()
    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=operation_id,
            shell_command="echo hi",
            argv=None,
            cwd="/workspace",
            environment=(EnvironmentVariable(name="A", value="1"),),
            tty=None,
            output_limit_bytes=1024,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )

    assert process_id == str(operation_id), "the caller's id is the handle"

    snapshot = await provider.read_process_output(
        instance,
        process_id=process_id,
        after_sequence=0,
        wait_seconds=0,
        deadline_at=_deadline(),
    )
    assert b"echo hi" in b"".join(chunk.data for chunk in snapshot.chunks)

    # A second read from the delivered position returns nothing new, which is
    # what makes polling cheap instead of re-delivering the whole buffer.
    again = await provider.read_process_output(
        instance,
        process_id=process_id,
        after_sequence=snapshot.next_sequence,
        wait_seconds=0,
        deadline_at=_deadline(),
    )
    assert again.chunks == ()


async def test_a_tty_process_streams_on_the_pty_channel(
    provider: E2BSandboxProvider, monkeypatch
) -> None:
    from app.modules.workspace.testing.fake_output_buffer import InMemoryOutputBuffer

    buffer = InMemoryOutputBuffer()
    monkeypatch.setattr(provider, "_output", buffer)
    monkeypatch.setattr(provider, "_remember_pid", buffer.remember_pid)
    monkeypatch.setattr(provider, "_recall_pid", buffer.recall_pid)

    instance = await provider.create(_spec(uuid4()))
    await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command="bash",
            argv=None,
            cwd="/workspace",
            environment=(),
            tty=TerminalSize(rows=24, cols=80),
            output_limit_bytes=1024,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )

    channels = {chunk.channel.value for chunk in buffer.all_chunks()}
    assert "pty" in channels


# ---------------------------------------------------------------------------
# Process lifetime, which E2B enforces and defaults to one minute
# ---------------------------------------------------------------------------


@pytest.fixture
def buffered_provider(provider: E2BSandboxProvider, monkeypatch) -> E2BSandboxProvider:
    from app.modules.workspace.testing.fake_output_buffer import InMemoryOutputBuffer

    buffer = InMemoryOutputBuffer()
    monkeypatch.setattr(provider, "_output", buffer)
    monkeypatch.setattr(provider, "_remember_pid", buffer.remember_pid)
    monkeypatch.setattr(provider, "_recall_pid", buffer.recall_pid)
    return provider


async def _start(provider: E2BSandboxProvider, *, deadline_at, tty=None) -> None:
    instance = await provider.create(_spec(uuid4()))
    await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command="npm run build",
            argv=None,
            cwd="/workspace",
            environment=(),
            tty=tty,
            output_limit_bytes=1024,
            deadline_at=deadline_at,
            initial_input=None,
        ),
        deadline_at=deadline_at,
    )


@pytest.mark.parametrize("tty", [None, TerminalSize(rows=24, cols=80)])
async def test_a_process_lives_as_long_as_its_deadline_says(
    buffered_provider: E2BSandboxProvider, world: FakeE2B, tty
) -> None:
    """The regression that cost every long build its last 59 minutes.

    E2B kills a command at `timeout` and defaults that to 60 seconds. This
    provider accepted a `deadline_at` on every operation and passed it to
    nothing, so a build given an hour was killed after a minute — mid-flight,
    while the agent was still polling it. Both process kinds are checked
    because they are started through different SDK calls with the same default.
    """
    await _start(
        buffered_provider,
        deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
        tty=tty,
    )

    assert world.process_timeouts, "no process was started"
    granted = world.process_timeouts[-1]
    assert granted is not None, "no lifetime passed: E2B would apply its 60s default"
    assert granted == pytest.approx(3600, abs=5)


async def test_an_expired_deadline_does_not_become_an_immortal_process(
    buffered_provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """E2B reads a non-positive timeout as "no timeout"."""
    await _start(
        buffered_provider,
        deadline_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    granted = world.process_timeouts[-1]
    assert granted is not None and granted > 0


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------


async def test_files_round_trip(provider: E2BSandboxProvider) -> None:
    instance = await provider.create(_spec(uuid4()))

    async def payload():
        yield b"contents"

    stat = await provider.write_file(
        instance,
        path="/workspace/a.txt",
        data=payload(),
        expected_sha256=None,
        deadline_at=_deadline(),
    )
    assert stat.path == "/workspace/a.txt"

    chunks = [
        chunk
        async for chunk in provider.open_file(
            instance,
            path="/workspace/a.txt",
            byte_range=ByteRange(offset=0, length=None),
            deadline_at=_deadline(),
        )
    ]
    assert b"".join(chunks) == b"contents"


async def test_a_missing_file_is_definitively_missing(
    provider: E2BSandboxProvider,
) -> None:
    """Not a transient failure, or a caller would retry a file that will never
    appear."""
    instance = await provider.create(_spec(uuid4()))
    with pytest.raises(SandboxPathNotFound):
        await provider.stat_file(
            instance, path="/workspace/nope.txt", deadline_at=_deadline()
        )


async def test_deleting_a_missing_file_reports_that_nothing_was_removed(
    provider: E2BSandboxProvider,
) -> None:
    instance = await provider.create(_spec(uuid4()))
    assert (
        await provider.delete_file(
            instance,
            path="/workspace/nope.txt",
            recursive=False,
            deadline_at=_deadline(),
        )
        is False
    )


async def test_a_mismatched_digest_is_refused_before_writing(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    instance = await provider.create(_spec(uuid4()))

    async def payload():
        yield b"contents"

    from sandbox_runtime.errors import SandboxRejected

    with pytest.raises(SandboxRejected, match="digest"):
        await provider.write_file(
            instance,
            path="/workspace/a.txt",
            data=payload(),
            expected_sha256="sha256:" + "0" * 64,
            deadline_at=_deadline(),
        )
    assert "/workspace/a.txt" not in world.files


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


async def test_a_missing_sandbox_is_definitively_gone(
    provider: E2BSandboxProvider, world: FakeE2B, monkeypatch
) -> None:
    """A caller must re-ensure rather than retry the same dead handle."""
    instance = await provider.create(_spec(uuid4()))
    world.sandboxes.clear()

    with pytest.raises(ProviderGone):
        await provider.stat_file(
            instance, path="/workspace/a.txt", deadline_at=_deadline()
        )


@pytest.mark.parametrize(
    "error,expected",
    [
        (RateLimitException("429 too many requests"), SandboxUnavailable),
        (AuthenticationException("401 unauthorized"), ProviderRejected),
        (NotFoundException("sandbox not found"), ProviderGone),
        (RuntimeError("connection reset by peer"), SandboxUnavailable),
    ],
)
async def test_sdk_failures_are_classified_not_swallowed(
    provider: E2BSandboxProvider, monkeypatch, error, expected
) -> None:
    """The provider says what happened; whether to wait is the service's call,
    because only it knows the caller's deadline."""

    class _Exploding(FakeSandboxSdk):
        @staticmethod
        def list(query=None, **_kwargs):
            raise error

    monkeypatch.setattr(type(provider), "_sdk", property(lambda self: _Exploding))

    with pytest.raises(expected):
        await provider.create(_spec(uuid4()))


async def test_rate_limiting_carries_a_retry_hint(
    provider: E2BSandboxProvider, monkeypatch
) -> None:
    class _Limited(FakeSandboxSdk):
        @staticmethod
        def list(query=None, **_kwargs):
            raise RateLimitException("429")

    monkeypatch.setattr(type(provider), "_sdk", property(lambda self: _Limited))

    with pytest.raises(SandboxUnavailable) as raised:
        await provider.create(_spec(uuid4()))
    assert raised.value.retry_after_ms and raised.value.retry_after_ms > 0


# ---------------------------------------------------------------------------
# Ports and reclamation
# ---------------------------------------------------------------------------


async def test_a_published_port_resolves_to_a_sandbox_host(
    provider: E2BSandboxProvider,
) -> None:
    instance = await provider.create(_spec(uuid4()))
    url = await provider.port_base_url(instance, port=4848, deadline_at=_deadline())
    assert url.startswith("https://4848-")


async def test_the_sweep_only_claims_sandboxes_carrying_our_metadata(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """The E2B account may be shared with something else entirely."""
    sandbox_id = uuid4()
    await provider.create(_spec(sandbox_id))
    world.sandboxes["someone-else"] = type(
        next(iter(world.sandboxes.values()))
    )(sandbox_id="someone-else", metadata={"team": "other"})

    objects = await provider.list_objects(deadline_at=_deadline())

    assert {obj.sandbox_id for obj in objects} == {sandbox_id}
