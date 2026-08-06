from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from agentbox.domain import (
    EnvironmentVariable,
    StartProcessRequest,
    TerminalSize,
)

from app.modules.workspace.domain.errors import (
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
    META_EPOCH,
    META_SANDBOX_ID,
    E2BProviderConfig,
    E2BSandboxProvider,
)
from app.modules.workspace.testing.fake_e2b import (
    AuthenticationException,
    FakeE2B,
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
    monkeypatch.setattr(
        type(instance), "_sdk", property(lambda self: world.sandbox_class())
    )
    monkeypatch.setattr(
        type(instance), "_volumes", property(lambda self: world.volume_class())
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


async def test_a_new_epoch_is_a_different_sandbox(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    """The fence has to hold on E2B too, or a stale handle reaches the
    replacement."""
    sandbox_id = uuid4()
    first = await provider.create(_spec(sandbox_id, epoch=1))
    second = await provider.create(
        _spec(
            sandbox_id,
            epoch=2,
            name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 2),
        )
    )

    assert first.provider_id != second.provider_id
    assert len(world.created) == 2


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
# Volumes
# ---------------------------------------------------------------------------


async def test_a_volume_is_found_by_name_and_mounted(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    sandbox_id = uuid4()
    name = naming.volume_name(sandbox_id, 1)
    await provider.ensure_volume(
        sandbox_id=sandbox_id, name=name, deadline_at=_deadline()
    )

    assert (
        await provider.find_volume(sandbox_id=sandbox_id, deadline_at=_deadline())
        == name
    )

    await provider.create(_spec(sandbox_id, volume_name=name))
    assert world.created[0]["volume_mounts"] is not None


async def test_ensure_volume_does_not_create_a_second_one(
    provider: E2BSandboxProvider, world: FakeE2B
) -> None:
    sandbox_id = uuid4()
    name = naming.volume_name(sandbox_id, 1)
    await provider.ensure_volume(
        sandbox_id=sandbox_id, name=name, deadline_at=_deadline()
    )
    await provider.ensure_volume(
        sandbox_id=sandbox_id, name=name, deadline_at=_deadline()
    )
    assert len(world.volumes) == 1


async def test_the_newest_generation_wins_when_an_old_disk_lingers(
    provider: E2BSandboxProvider,
) -> None:
    """A replaced disk may leave its volume behind; adopting the old one would
    hand the user back files they already lost."""
    sandbox_id = uuid4()
    for generation in (1, 2):
        await provider.ensure_volume(
            sandbox_id=sandbox_id,
            name=naming.volume_name(sandbox_id, generation),
            deadline_at=_deadline(),
        )

    assert await provider.find_volume(
        sandbox_id=sandbox_id, deadline_at=_deadline()
    ) == naming.volume_name(sandbox_id, 2)


async def test_another_sandboxes_volume_is_never_adopted(
    provider: E2BSandboxProvider,
) -> None:
    theirs = uuid4()
    await provider.ensure_volume(
        sandbox_id=theirs,
        name=naming.volume_name(theirs, 1),
        deadline_at=_deadline(),
    )
    assert (
        await provider.find_volume(sandbox_id=uuid4(), deadline_at=_deadline())
        is None
    )


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
            byte_range=__import__(
                "agentbox.domain", fromlist=["ByteRange"]
            ).ByteRange(offset=0, length=None),
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

    from app.modules.workspace.domain.errors import SandboxRejected

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

    class _Exploding:
        @staticmethod
        def list(query=None, **_kwargs):
            raise error

    monkeypatch.setattr(type(provider), "_sdk", property(lambda self: _Exploding))

    with pytest.raises(expected):
        await provider.create(_spec(uuid4()))


async def test_rate_limiting_carries_a_retry_hint(
    provider: E2BSandboxProvider, monkeypatch
) -> None:
    class _Limited:
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
