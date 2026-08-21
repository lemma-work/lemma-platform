from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.workspace.domain.sandbox import SandboxKind, SandboxMount
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    LABEL_MANAGED_BY,
    LABEL_SANDBOX_ID,
    MANAGED_BY,
    ProviderCreateAmbiguous,
    ProviderCreateSpec,
    ProviderRejected,
)
from app.modules.workspace.providers.docker import (
    DockerProviderConfig,
    DockerSandboxProvider,
    RuntimeCredentialSigner,
)
from app.modules.workspace.providers.docker_engine import (
    DockerRequestAmbiguous,
    DockerVolume,
)
from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeError
from app.modules.workspace.testing.fake_docker_engine import FakeDockerEngine

pytestmark = pytest.mark.asyncio

PINNED_IMAGE = "lemma-workspace@sha256:" + "b" * 64


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def _provider(engine: FakeDockerEngine, **overrides) -> DockerSandboxProvider:
    return DockerSandboxProvider(
        engine,  # type: ignore[arg-type]
        DockerProviderConfig(**overrides),
        RuntimeCredentialSigner(key=b"k" * 32),
    )


def _spec(sandbox_id, *, epoch: int = 1, **overrides) -> ProviderCreateSpec:
    defaults = dict(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=epoch,
        name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, epoch),
        image=PINNED_IMAGE,
        profile_name="workspace-python-v1",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=_deadline(),
    )
    defaults.update(overrides)
    return ProviderCreateSpec(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The data-loss guard
# ---------------------------------------------------------------------------


async def test_a_volume_created_after_cutover_is_preferred() -> None:
    engine = FakeDockerEngine()
    sandbox_id = uuid4()
    engine.volumes["lemma-vol-new"] = DockerVolume.model_validate(
        {
            "Name": "lemma-vol-new",
            "Labels": {
                LABEL_MANAGED_BY: MANAGED_BY,
                LABEL_SANDBOX_ID: str(sandbox_id),
            },
        }
    )
    adopted = await _provider(engine).find_volume(
        sandbox_id=sandbox_id, deadline_at=_deadline()
    )
    assert adopted == "lemma-vol-new"


async def test_a_fresh_sandbox_has_nothing_to_adopt() -> None:
    engine = FakeDockerEngine()
    assert (
        await _provider(engine).find_volume(sandbox_id=uuid4(), deadline_at=_deadline())
        is None
    )


async def test_ensure_volume_is_idempotent() -> None:
    engine = FakeDockerEngine()
    provider = _provider(engine)
    sandbox_id = uuid4()
    name = naming.volume_name(sandbox_id, 1)

    first = await provider.ensure_volume(
        sandbox_id=sandbox_id, name=name, deadline_at=_deadline()
    )
    second = await provider.ensure_volume(
        sandbox_id=sandbox_id, name=name, deadline_at=_deadline()
    )

    assert first == second == name
    assert len(engine.volumes) == 1
    # Created with our labels, so the next adoption finds it on the fast path.
    assert engine.volumes[name].labels[LABEL_SANDBOX_ID] == str(sandbox_id)


# ---------------------------------------------------------------------------
# Idempotent create
# ---------------------------------------------------------------------------


async def test_creating_twice_yields_one_container() -> None:
    """The whole reason a create-attempt ledger is unnecessary."""
    engine = FakeDockerEngine()
    provider = _provider(engine)
    sandbox_id = uuid4()

    first = await provider.create(_spec(sandbox_id))
    second = await provider.create(_spec(sandbox_id))

    assert first.provider_id == second.provider_id
    assert len(engine.containers) == 1
    # The second call short-circuits on inspect rather than asking Docker.
    assert engine.create_calls.count(first.name) == 1


async def test_a_lost_create_response_is_recovered_by_looking(monkeypatch) -> None:
    """A concurrent creator winning the name race is success, not failure."""
    engine = FakeDockerEngine()
    provider = _provider(engine)
    sandbox_id = uuid4()
    spec = _spec(sandbox_id)

    # Somebody else creates it while our request is in flight.
    async def racing_create(name, request, *, deadline_at):
        engine._next_id += 1
        from app.modules.workspace.testing.fake_docker_engine import FakeContainer

        engine.containers[name] = FakeContainer(
            container_id="raced",
            name=name,
            image=request.image,
            labels=dict(request.labels),
        )
        from app.modules.workspace.providers.docker_engine import DockerEngineError

        raise DockerEngineError(f"Conflict. The container name {name} is in use")

    monkeypatch.setattr(engine, "create_container", racing_create)
    instance = await provider.create(spec)
    assert instance.provider_id == "raced"


async def test_an_unreachable_engine_is_reported_as_ambiguous() -> None:
    """Only the genuinely unresolvable case keeps the ambiguous type."""
    engine = FakeDockerEngine()
    engine.fail_next_create = DockerRequestAmbiguous("connection reset")
    with pytest.raises(ProviderCreateAmbiguous):
        await _provider(engine).create(_spec(uuid4()))


async def test_unpinned_images_are_refused() -> None:
    engine = FakeDockerEngine()
    with pytest.raises(ProviderRejected, match="sha256"):
        await _provider(engine).create(_spec(uuid4(), image="workspace:latest"))

    # ...unless the deployment has opted into mutable tags.
    lenient = _provider(engine, allow_mutable_images=True)
    assert await lenient.create(_spec(uuid4(), image="workspace:latest"))


# ---------------------------------------------------------------------------
# Fencing and reclamation
# ---------------------------------------------------------------------------


async def test_a_new_epoch_gets_a_container_the_old_one_cannot_name() -> None:
    engine = FakeDockerEngine()
    provider = _provider(engine)
    sandbox_id = uuid4()

    old = await provider.create(_spec(sandbox_id, epoch=1))
    new = await provider.create(
        _spec(
            sandbox_id,
            epoch=2,
            name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 2),
        )
    )

    assert old.name != new.name
    assert old.provider_id != new.provider_id


async def test_a_container_from_the_same_profile_build_is_reused() -> None:
    """The idempotence the fence must not break: a retry after a lost response
    finds the container rather than creating a second one."""
    engine = FakeDockerEngine()
    provider = _provider(engine)
    sandbox_id = uuid4()

    first = await provider.create(_spec(sandbox_id))
    second = await provider.create(_spec(sandbox_id))

    assert first.provider_id == second.provider_id


async def test_a_container_from_an_older_profile_build_is_replaced() -> None:
    """Releasing a new sandbox image has to reach workspaces that already
    exist. Reuse keyed only on the name would leave this container running the
    image it was born with for as long as the workspace lives, so a fix shipped
    in the image would never arrive. The volume is a separate object and is
    adopted, so the user's files survive the replacement.
    """
    engine = FakeDockerEngine()
    provider = _provider(engine)
    sandbox_id = uuid4()

    old = await provider.create(_spec(sandbox_id, profile_digest="sha256:" + "a" * 64))
    new = await provider.create(_spec(sandbox_id, profile_digest="sha256:" + "b" * 64))

    assert new.provider_id != old.provider_id, "the stale container must be replaced"
    assert new.volume_name == old.volume_name, "the files must not go with it"


async def test_an_operation_against_a_destroyed_container_is_definitively_gone() -> (
    None
):
    """The fence firing must not look like a transient error, or a caller
    would retry forever instead of re-ensuring."""
    from app.modules.workspace.providers.base import ProviderGone

    engine = FakeDockerEngine()
    provider = _provider(engine)
    sandbox_id = uuid4()
    instance = await provider.create(_spec(sandbox_id))
    await provider.destroy(instance.name, deadline_at=_deadline())

    with pytest.raises(ProviderGone):
        await provider._runtime_client(instance.provider_id, deadline_at=_deadline())


async def test_the_sweep_ignores_containers_that_are_not_ours() -> None:
    engine = FakeDockerEngine()
    from app.modules.workspace.testing.fake_docker_engine import FakeContainer

    engine.containers["postgres"] = FakeContainer(
        container_id="pg", name="postgres", image="postgres:18", labels={}
    )

    assert await _provider(engine).list_objects(deadline_at=_deadline()) == ()


# ---------------------------------------------------------------------------
# Container shape
# ---------------------------------------------------------------------------


async def test_a_workspace_binds_its_volume_at_the_workspace_root() -> None:
    engine = FakeDockerEngine()
    sandbox_id = uuid4()
    await _provider(engine).create(_spec(sandbox_id, volume_name="ab-ws-adopted"))

    container = engine.containers[
        naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1)
    ]
    assert container.labels["workspace-storage-id"] == "ab-ws-adopted"


async def test_local_mounts_are_bound_alongside_the_volume() -> None:
    """Phase 3 rides on this: a bound host folder is just another bind."""
    engine = FakeDockerEngine()
    sandbox_id = uuid4()
    spec = _spec(
        sandbox_id,
        volume_name="lemma-vol-1",
        mounts=(
            SandboxMount(
                host_path="/Users/me/code",
                container_path="/workspace/code",
                read_only=True,
            ),
        ),
    )
    instance = await _provider(engine).create(spec)
    assert instance.volume_name == "lemma-vol-1"


async def test_a_function_sandbox_gets_a_read_only_root() -> None:
    """Function control state lives in /tmp, so the root being writable would
    silently allow state the immutable-artifact contract forbids."""
    engine = FakeDockerEngine()
    sandbox_id = uuid4()
    name = naming.container_name(sandbox_id, SandboxKind.FUNCTION, 1)
    await _provider(engine).create(
        _spec(sandbox_id, kind=SandboxKind.FUNCTION, name=name)
    )
    assert name in engine.containers


async def test_release_stops_the_container_but_keeps_the_volume() -> None:
    engine = FakeDockerEngine()
    provider = _provider(engine)
    sandbox_id = uuid4()
    instance = await provider.create(_spec(sandbox_id, volume_name="ab-ws-keep"))
    await engine.start_container(instance.provider_id, deadline_at=_deadline())

    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    assert engine.stopped == [instance.name]
    assert instance.name in engine.containers
    # Suspending compute must never take the user's files with it.
    assert engine.destroyed == []
    assert "ab-ws-keep" not in [v for v in engine.volumes]


async def test_an_unreachable_runtime_still_gets_stopped() -> None:
    """Quiesce is best effort. The workspace whose runtime has wedged is
    precisely the one that must still be stoppable, so a failure to reach it
    cannot be allowed to abort the release."""
    engine = FakeDockerEngine()
    provider = _provider(engine)
    instance = await provider.create(_spec(uuid4()))

    async def unreachable(*args, **kwargs):
        raise WorkspaceRuntimeError("runtime is not answering")

    provider._runtime_client = unreachable  # type: ignore[method-assign]

    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    assert engine.stopped == [instance.name]
