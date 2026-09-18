"""Installing the overlay must be free when nothing changed, and safe when it fails.

Two properties carry this feature. The first is cost: it runs on the way to every
tool call, on a path whose remaining expense is already one provider round trip,
so "already installed" has to be answerable without touching the sandbox at all.
The second is blast radius: a sandbox that cannot take the overlay still has the
copy baked into its image, so a failure here must cost staleness rather than the
caller's tool call.

Both are asserted against a client that counts what it was asked to do, because
both are claims about I/O that no amount of reading the code can settle.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.infrastructure.runtime_bundle import (
    RuntimeBundle,
    load_bundle,
)
from app.modules.workspace.services.workspace_runtime_bundle import (
    _REMEMBERED_SANDBOXES,
    WorkspaceRuntimeBundleMixin,
)
from sandbox_runtime.errors import SandboxUnavailable
from sandbox_runtime.protocol import ProcessOutputSnapshot, ProcessState

_BUNDLE = RuntimeBundle(
    version="sha256:" + "a" * 64,
    archive=b"PK\x03\x04 pretend this is a zip",
    archive_sha256="sha256:" + "b" * 64,
    requires=("lemma_sdk",),
)


class _Client:
    """A manager client that records what it was asked to do."""

    def __init__(
        self, *, installed: str | None = None, exit_code: int = 0, fail: bool = False
    ) -> None:
        self.installed = installed
        self.exit_code = exit_code
        self.fail = fail
        self.reads: list[str] = []
        self.writes: list[tuple[str, int]] = []
        self.commands: list[str] = []

    async def read_file(self, _user_id: UUID, path: str, **_kwargs: Any) -> bytes:
        self.reads.append(path)
        if self.installed is None:
            raise SandboxUnavailable("no such file")
        return self.installed.encode()

    async def write_file(
        self, _user_id: UUID, path: str, data: bytes, **_kwargs: Any
    ) -> None:
        if self.fail:
            raise SandboxUnavailable("the sandbox went away")
        self.writes.append((path, len(data)))

    async def start_process(
        self, _kind: Any, _user_id: UUID, *, shell_command: str, **_kwargs: Any
    ) -> None:
        self.commands.append(shell_command)

    async def read_process_output(
        self, _kind: Any, _user_id: UUID, _operation_id: UUID, **_kwargs: Any
    ) -> ProcessOutputSnapshot:
        return ProcessOutputSnapshot(
            chunks=(),
            next_sequence=1,
            truncated_before_sequence=None,
            state=(
                ProcessState.SUCCEEDED if self.exit_code == 0 else ProcessState.FAILED
            ),
            exit_code=self.exit_code,
        )


class _Service(WorkspaceRuntimeBundleMixin):
    def __init__(self, client: _Client, bundle: RuntimeBundle | None = _BUNDLE) -> None:
        self.client = client
        self.bundle = bundle
        # Per-instance, so one test's remembered install cannot leak into the
        # next through the class-level cache the real service shares. Same
        # type as the subject's: the eviction it does is ordering-dependent.
        self._installed_bundles = OrderedDict()
        self._inflight_bundles = {}

    def _get_manager_client(self) -> _Client:
        return self.client

    def _runtime_bundle(self) -> RuntimeBundle | None:
        return self.bundle


def _info(*, allocation: str = "alloc-1", generation: int = 1) -> SandboxInfo:
    return SandboxInfo(
        sandbox_id=str(uuid4()),
        status="RUNNING",
        image="",
        allocation_id=allocation,
        allocation_epoch=1,
        storage_generation=generation,
    )


async def test_a_sandbox_without_the_overlay_gets_it_installed() -> None:
    client = _Client(installed=None)
    service = _Service(client)

    await service._ensure_runtime_bundle(uuid4(), _info())

    assert client.writes, "the bundle was never delivered"
    assert any(path.endswith(".zip") for path, _ in client.writes)
    assert len(client.commands) == 1
    assert _BUNDLE.version in client.commands[0]


async def test_a_sandbox_already_on_this_version_is_left_alone() -> None:
    """The stamp is the sandbox's own claim, and it is believed."""
    client = _Client(installed=_BUNDLE.version)
    service = _Service(client)

    await service._ensure_runtime_bundle(uuid4(), _info())

    assert client.reads, "the sandbox was never asked"
    assert client.writes == []
    assert client.commands == []


async def test_the_second_ensure_asks_the_sandbox_nothing_at_all() -> None:
    """The warm path is the one that runs on every tool call.

    Not "does little I/O" -- none. A client that raises on any call is the only
    way to state that as a fact rather than an intention.
    """
    client = _Client(installed=None)
    service = _Service(client)
    user_id = uuid4()
    info = _info()
    await service._ensure_runtime_bundle(user_id, info)

    class _Refuses(_Client):
        async def read_file(self, *_args: Any, **_kwargs: Any) -> bytes:
            raise AssertionError("the warm path touched the sandbox")

        async def write_file(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("the warm path touched the sandbox")

    service.client = _Refuses()

    await service._ensure_runtime_bundle(user_id, info)


async def test_a_replaced_sandbox_is_asked_again() -> None:
    """The overlay lives in the sandbox, not on the disk behind it.

    A new allocation has no overlay however healthy the disk is, so a remembered
    "installed" from the previous one would leave the new sandbox running the
    image's older copy while we believed otherwise.
    """
    client = _Client(installed=None)
    service = _Service(client)
    user_id = uuid4()
    await service._ensure_runtime_bundle(user_id, _info(allocation="alloc-1"))
    before = len(client.commands)

    await service._ensure_runtime_bundle(user_id, _info(allocation="alloc-2"))

    assert len(client.commands) == before + 1


async def test_the_memory_of_installs_does_not_grow_without_bound() -> None:
    """The key names an allocation, and allocations keep being made.

    Nothing releases an entry when its sandbox goes, so a long-lived process
    under sustained churn would hold one key per sandbox it had ever seen. The
    entry evicted is the one whose sandbox was touched longest ago, and the only
    cost of evicting a live one is a re-probe.
    """
    client = _Client(installed=None)
    service = _Service(client)
    user_id = uuid4()
    for n in range(_REMEMBERED_SANDBOXES + 2):
        await service._ensure_runtime_bundle(user_id, _info(allocation=f"alloc-{n}"))

    assert len(service._installed_bundles) == _REMEMBERED_SANDBOXES
    remembered = {key[2] for key in service._installed_bundles}
    assert "alloc-0" not in remembered
    assert f"alloc-{_REMEMBERED_SANDBOXES + 1}" in remembered


async def test_a_sandbox_still_in_use_stays_remembered_while_others_age_out() -> None:
    """Eviction is by last use, not by when the install happened.

    Insertion order alone would evict the sandbox someone has been working in
    all day ahead of one that was installed into once and abandoned -- exactly
    backwards, since the busy one is the whole reason this path avoids I/O.

    `busy` is installed first and `idle` second, so under insertion order `busy`
    goes first; the hit in between is what has to reorder them.
    """
    client = _Client(installed=None)
    service = _Service(client)
    user_id = uuid4()
    await service._ensure_runtime_bundle(user_id, _info(allocation="busy"))
    await service._ensure_runtime_bundle(user_id, _info(allocation="idle"))
    await service._ensure_runtime_bundle(user_id, _info(allocation="busy"))

    # One more than the bound holds, so exactly one entry is evicted -- and
    # `busy` is never touched again until the assertion.
    for n in range(_REMEMBERED_SANDBOXES - 1):
        await service._ensure_runtime_bundle(user_id, _info(allocation=f"alloc-{n}"))

    remembered = {key[2] for key in service._installed_bundles}
    assert "busy" in remembered
    assert "idle" not in remembered


async def test_a_failed_delivery_does_not_fail_the_caller() -> None:
    """The sandbox keeps the image's copy, so staleness is the whole cost.

    Failing a tool call because an upload timed out would be a worse outcome
    than the staleness it was trying to fix.
    """
    client = _Client(installed=None, fail=True)
    service = _Service(client)

    await service._ensure_runtime_bundle(uuid4(), _info())

    assert client.commands == []


async def test_an_installer_that_exits_non_zero_does_not_fail_the_caller() -> None:
    client = _Client(installed=None, exit_code=1)
    service = _Service(client)

    await service._ensure_runtime_bundle(uuid4(), _info())


async def test_a_failed_install_is_retried_rather_than_remembered() -> None:
    """A remembered failure would pin the sandbox to the old copy for good."""
    client = _Client(installed=None, fail=True)
    service = _Service(client)
    user_id = uuid4()
    info = _info()
    await service._ensure_runtime_bundle(user_id, info)

    service.client = working = _Client(installed=None)
    await service._ensure_runtime_bundle(user_id, info)

    assert len(working.commands) == 1


async def test_concurrent_callers_collapse_to_one_install() -> None:
    """Otherwise every tool call in a herd uploads the same 1.6 MB."""
    client = _Client(installed=None)
    service = _Service(client)
    user_id = uuid4()
    info = _info()

    await asyncio.gather(
        *(service._ensure_runtime_bundle(user_id, info) for _ in range(5))
    )

    assert len(client.commands) == 1


async def test_a_deployment_with_no_bundle_does_nothing() -> None:
    """Docker and lemma_local have not adopted the overlay, and still work."""
    client = _Client(installed=None)
    service = _Service(client, bundle=None)

    await service._ensure_runtime_bundle(uuid4(), _info())

    assert client.reads == []
    assert client.writes == []


async def test_the_archive_is_delivered_with_its_digest_for_the_provider_to_check() -> (
    None
):
    """A truncated upload must be refused before anything unpacks it."""
    seen: dict[str, Any] = {}

    class _Checking(_Client):
        async def write_file(
            self, _user_id: UUID, path: str, data: bytes, **kwargs: Any
        ) -> None:
            seen[path] = kwargs.get("expected_sha256")
            self.writes.append((path, len(data)))

    service = _Service(_Checking(installed=None))

    await service._ensure_runtime_bundle(uuid4(), _info())

    archive = next(path for path in seen if path.endswith(".zip"))
    assert seen[archive] == "b" * 64


def test_a_directory_without_a_manifest_holds_no_bundle(tmp_path: Path) -> None:
    """Absence is a valid state, not an error.

    A Docker deployment has no bundle and a working sandbox; a backend that
    refused to serve without one would break the fabric that never needed it.
    """
    assert load_bundle(tmp_path) is None


def test_a_manifest_whose_archive_is_missing_holds_no_bundle(tmp_path: Path) -> None:
    """Half a bundle must read as none, not as one that fails on delivery."""
    (tmp_path / "manifest.json").write_text(
        '{"version": "sha256:cafe", "archive": "runtime-bundle.zip", '
        '"archive_sha256": "sha256:beef", "requires": []}',
        encoding="utf-8",
    )

    assert load_bundle(tmp_path) is None


def test_a_bundle_directory_is_read_whole(tmp_path: Path) -> None:
    (tmp_path / "runtime-bundle.zip").write_bytes(b"PK\x03\x04 payload")
    (tmp_path / "manifest.json").write_text(
        '{"version": "sha256:cafe", "archive": "runtime-bundle.zip", '
        '"archive_sha256": "sha256:beef", "requires": ["lemma_sdk"]}',
        encoding="utf-8",
    )

    loaded = load_bundle(tmp_path)

    assert loaded is not None
    assert loaded.version == "sha256:cafe"
    assert loaded.archive == b"PK\x03\x04 payload"
    assert loaded.requires == ("lemma_sdk",)
