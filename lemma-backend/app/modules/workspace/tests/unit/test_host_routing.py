"""Routing between the VM and the user's Mac, by the sandbox id alone.

The choice is recorded in a host sandbox's id, so these assert that the id is
unmistakable, that every call on a host sandbox reaches the host and every
other call reaches the configured provider, and that a host session keeps the
Mac's own paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4, uuid7

import pytest

from app.modules.workspace.domain.host_execution import (
    HOST_EXECUTION_PROVIDER,
    HostTarget,
    RunHostPin,
    host_sandbox_id,
    host_sandbox_slug,
    is_host_sandbox_id,
    pinned_host_for,
    run_pinned_host,
)
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.host_workspace_session import (
    HostWorkspaceSession,
    host_path,
)
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    ProviderInstance,
    ProviderStorageKind,
    provider_name_for,
    storage_kind_for,
)
from app.modules.workspace.providers.host_routing import HostRoutingProvider


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=5)


class RecordingProvider:
    """Answers every call it is given by writing its own name down."""

    def __init__(self, name: str, storage_kind: ProviderStorageKind) -> None:
        self.name = name
        self.storage_kind = storage_kind
        self.calls: list[str] = []

    async def inspect(self, name, *, deadline_at):
        self.calls.append("inspect")
        return ProviderInstance(provider_id=name, name=name, running=True)

    async def stat_file(self, instance, *, path, deadline_at):
        self.calls.append("stat_file")
        return path

    async def find_volume(self, *, sandbox_id, deadline_at):
        self.calls.append("find_volume")
        return

    async def list_objects(self, *, deadline_at):
        self.calls.append("list_objects")
        return ()

    async def destroy(self, name, *, deadline_at):
        self.calls.append("destroy")

    async def close(self):
        self.calls.append("close")


@pytest.fixture
def default() -> RecordingProvider:
    return RecordingProvider("lemma_local", ProviderStorageKind.VOLUME)


@pytest.fixture
def host() -> RecordingProvider:
    return RecordingProvider(
        HOST_EXECUTION_PROVIDER, ProviderStorageKind.SANDBOX_NATIVE
    )


@pytest.fixture
def routing(default, host) -> HostRoutingProvider:
    return HostRoutingProvider(default, host)


def _instance(sandbox_id) -> ProviderInstance:
    name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1)
    return ProviderInstance(provider_id=name, name=name)


def test_a_host_sandbox_id_is_deterministic_and_unmistakable():
    conversation = uuid4()

    assert host_sandbox_id(conversation) == host_sandbox_id(conversation)
    assert host_sandbox_id(conversation) != host_sandbox_id(uuid4())
    assert is_host_sandbox_id(host_sandbox_id(conversation))
    # Workspaces are user ids and function sandboxes pod ids: v4 or v7.
    assert not is_host_sandbox_id(conversation)
    assert not is_host_sandbox_id(uuid7())


async def test_calls_on_a_host_sandbox_reach_only_the_host(routing, default, host):
    instance = _instance(host_sandbox_id(uuid4()))

    await routing.inspect(instance.name, deadline_at=_deadline())
    await routing.stat_file(instance, path="/Users/o/a", deadline_at=_deadline())
    await routing.destroy(instance.name, deadline_at=_deadline())

    assert host.calls == ["inspect", "stat_file", "destroy"]
    assert default.calls == []


async def test_every_other_sandbox_is_untouched(routing, default, host):
    user_workspace = _instance(uuid7())

    await routing.inspect(user_workspace.name, deadline_at=_deadline())
    await routing.stat_file(
        user_workspace, path="/home/user/a", deadline_at=_deadline()
    )
    await routing.find_volume(sandbox_id=uuid7(), deadline_at=_deadline())

    assert default.calls == ["inspect", "stat_file", "find_volume"]
    assert host.calls == []


async def test_sweeps_see_only_the_default_fabric(routing, default, host):
    await routing.list_objects(deadline_at=_deadline())

    assert default.calls == ["list_objects"]
    assert host.calls == []


async def test_close_closes_both(routing, default, host):
    await routing.close()
    assert default.calls == ["close"] and host.calls == ["close"]


def test_the_recorded_provider_and_storage_follow_the_sandbox(routing, default):
    on_host = host_sandbox_id(uuid4())
    in_vm = uuid7()

    assert provider_name_for(routing, on_host) == HOST_EXECUTION_PROVIDER
    assert provider_name_for(routing, in_vm) == "lemma_local"
    assert storage_kind_for(routing, on_host) is ProviderStorageKind.SANDBOX_NATIVE
    assert storage_kind_for(routing, in_vm) is ProviderStorageKind.VOLUME
    # A plain provider answers for itself, as before.
    assert provider_name_for(default, on_host) == "lemma_local"
    assert storage_kind_for(default, on_host) is ProviderStorageKind.VOLUME


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("notes.md", "/Users/o/proj/notes.md"),
        ("src/../README.md", "/Users/o/proj/README.md"),
        ("/tmp/x", "/tmp/x"),
        ("/Users/o/other/y", "/Users/o/other/y"),
    ],
)
def test_host_paths_are_kept_and_relative_ones_resolve_at_the_root(path, expected):
    assert host_path(path, base="/Users/o/proj") == expected


async def test_a_host_session_starts_in_the_root_and_never_rewrites_it():
    root = "/Users/o/lemma/c/2026-09-25/abc"
    session = HostWorkspaceSession(
        root=root, client=object(), sandbox_id=host_sandbox_id(uuid4())
    )

    assert session._cwd == root
    assert await session._resolve_path("a.txt") == f"{root}/a.txt"
    with pytest.raises(ValueError):
        HostWorkspaceSession(root="relative", client=object(), sandbox_id=uuid4())


# ------------------------------------------------ a run keeps its own Mac


class PinReadingClient:
    """Records, for each call, which host a pinned run would route it to."""

    def __init__(self) -> None:
        self.seen: list[RunHostPin | None] = []

    async def list_processes(self, workload_kind, logical_id, **_kwargs):
        self.seen.append(pinned_host_for(logical_id))
        return []

    def not_a_call(self) -> str:
        return "plain"


async def test_a_host_session_routes_every_call_by_its_runs_recorded_host():
    sandbox_id, host_id = host_sandbox_id(uuid4()), uuid4()
    root = "/Users/o/lemma/c/2026-09-25/abc"
    client = PinReadingClient()
    session = HostWorkspaceSession(
        root=root, host_id=host_id, client=client, sandbox_id=sandbox_id
    )

    await session.client.list_processes(None, sandbox_id)
    assert client.seen == [
        RunHostPin(sandbox_id=sandbox_id, host_id=host_id, root=root)
    ]
    # The pin lasts for the call only, and other sandboxes are never pinned.
    assert pinned_host_for(sandbox_id) is None
    await session.client.list_processes(None, uuid4())
    assert client.seen[-1] is None
    assert session.client.not_a_call() == "plain"


async def test_a_session_without_a_recorded_host_is_not_pinned():
    sandbox_id = host_sandbox_id(uuid4())
    client = PinReadingClient()
    session = HostWorkspaceSession(
        root="/Users/o/proj", client=client, sandbox_id=sandbox_id
    )
    await session.client.list_processes(None, sandbox_id)
    assert client.seen == [None]


class _Uow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


async def test_host_targets_follow_the_calling_run_not_the_conversations_latest(
    monkeypatch,
):
    """Two runs of one conversation chose different Macs: each run's ops go to
    its own, and only an unpinned caller follows the conversation's latest."""
    from types import SimpleNamespace

    from app.modules.agent.contracts import host_execution as contract
    from app.modules.workspace.services import host_workspace

    conversation_id = uuid4()
    sandbox_id = host_sandbox_id(conversation_id)
    run_mac, latest_mac = uuid4(), uuid4()

    class Repo:
        def __init__(self, _uow) -> None:
            pass

        async def get(self, _sandbox_id):
            return SimpleNamespace(
                slug=host_sandbox_slug(conversation_id), owner_id=uuid4()
            )

    async def latest(*, conversation_id, user_id):
        return latest_mac, "/Users/o/latest"

    monkeypatch.setattr(host_workspace, "SandboxRepository", Repo)
    monkeypatch.setattr(contract, "host_for_host_sandbox", latest)
    targets = host_workspace.SqlHostTargets(uow_factory=_Uow)

    with run_pinned_host(
        RunHostPin(sandbox_id=sandbox_id, host_id=run_mac, root="/Users/o/mine")
    ):
        pinned = await targets.target(sandbox_id)
    unpinned = await targets.target(sandbox_id)

    assert pinned == HostTarget(
        host_id=run_mac, conversation_id=conversation_id, root="/Users/o/mine"
    )
    assert unpinned == HostTarget(
        host_id=latest_mac, conversation_id=conversation_id, root="/Users/o/latest"
    )
