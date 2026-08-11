"""A workspace sandbox in the real macOS guest, and what survives a release.

`test_lemma_local_real_bridge.py` proves everything between the provider and
the guest boundary, with a Python stub standing in for the guest itself. It
says so in its own docstring, and the gap is not theoretical: the stub once
agreed with the provider that `sandbox.list` returns a top-level `sandbox_id`
when the real guest nests it at `status.id`.

This closes that gap. Nothing here is stubbed — the provider drives the shipped
`lemma-runtime` bridge, which talks to the guest daemon inside the
Virtualization.framework VM Lemma Desktop boots, which starts a real container
from a real image.

The property under test is the one a user notices: **a workspace's files
outlive its compute.** Releasing a sandbox stops the container and keeps the
disk, so the next tool call is slower but nothing is lost. That contract spans
the provider, the guest, and the volume the guest mounts, and no test below the
guest boundary can observe it.

## Running it

Needs a Lemma Desktop install whose runtime is up — the VM has to be booted,
because these tests do not boot it:

    open -a Lemma            # let Local finish starting

    export LEMMA_GUEST_CONTROL_SOCKET=~/Library/Application\\ Support/Lemma/\\
    locald/runtime/macos/control.sock
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from sandbox_runtime.protocol import ByteRange

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import ProviderCreateSpec
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.providers.lemma_local import (
    LemmaLocalProviderConfig,
    LemmaLocalSandboxProvider,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.local_guest,
    pytest.mark.asyncio,
]

# Long enough that a cold image pull inside the guest is not read as a hang.
GUEST_DEADLINE_SECONDS = 600


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


@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
async def sandbox(guest_provider):
    """One workspace sandbox, destroyed with its volume however the test ends.

    Cleanup is unconditional because a leak here is not a stray row — it is a
    container and a disk inside a VM that outlives the test session.
    """

    sandbox_id = uuid4()
    kind = SandboxKind.WORKSPACE
    name = naming.container_name(sandbox_id, kind, 1)
    volume_name = naming.volume_name(sandbox_id, 1)

    instance = await guest_provider.create(
        ProviderCreateSpec(
            sandbox_id=sandbox_id,
            kind=kind,
            epoch=1,
            name=name,
            image=os.environ["WORKSPACE_IMAGE"],
            profile_name="workspace",
            profile_digest="sha256:" + "a" * 64,
            deadline_at=_deadline(),
            volume_name=volume_name,
        )
    )
    instance = await guest_provider.wait_ready(instance, deadline_at=_deadline())
    try:
        yield guest_provider, instance, sandbox_id, kind, name, volume_name
    finally:
        await guest_provider.destroy(name, deadline_at=_deadline(120))
        await guest_provider.purge_storage(
            LemmaLocalSandboxProvider._guest_id(sandbox_id, kind),
            deadline_at=_deadline(120),
        )


async def test_a_workspace_sandbox_reaches_ready_in_the_real_guest(sandbox) -> None:
    """The whole chain, once: provider to bridge to guest to a live container.

    Everything below asserts on a sandbox that got here, so when this fails the
    rest is noise.
    """

    provider, instance, *_ = sandbox

    assert instance.provider_id, "the guest must name the container it started"
    inspected = await provider.inspect(instance, deadline_at=_deadline(120))
    assert inspected is not None, "a sandbox that just went ready must be findable"
    assert inspected.runtime_url, (
        "the runtime app has to be reachable or no tool call can land"
    )


async def test_files_written_to_a_workspace_outlive_its_container(sandbox) -> None:
    """Release stops compute and keeps the disk — the reason it is safe to sweep.

    An idle workspace is released automatically after
    WORKSPACE_IDLE_RELEASE_SECONDS. If the volume did not survive that, the
    sweep would silently destroy work, and the failure would surface hours
    later as "my files are gone" rather than as anything a log would show.
    """

    provider, instance, sandbox_id, kind, name, volume_name = sandbox
    marker = f"persisted-{uuid4().hex}"
    path = "/workspace/persistence-check.txt"

    async def chunks():
        yield marker.encode()

    written = await provider.write_file(
        instance,
        path=path,
        data=chunks(),
        expected_sha256=None,
        deadline_at=_deadline(120),
    )
    assert written.path == path

    # Release, not destroy: this is the sweep's move.
    await provider.release(instance, deadline_at=_deadline(120))

    # The same sandbox id and volume, a new container: what a tool call after
    # an idle release actually does.
    revived = await provider.create(
        ProviderCreateSpec(
            sandbox_id=sandbox_id,
            kind=kind,
            epoch=1,
            name=name,
            image=os.environ["WORKSPACE_IMAGE"],
            profile_name="workspace",
            profile_digest="sha256:" + "a" * 64,
            deadline_at=_deadline(),
            volume_name=volume_name,
        )
    )
    revived = await provider.wait_ready(revived, deadline_at=_deadline())

    restored = b"".join(
        [
            chunk
            async for chunk in provider.open_file(
                revived,
                path=path,
                byte_range=ByteRange(offset=0, length=None),
                deadline_at=_deadline(120),
            )
        ]
    )
    assert restored == marker.encode(), (
        "a released workspace must come back with its files; losing them here "
        "means the idle sweep destroys user work"
    )


async def test_a_workspace_volume_is_not_shared_between_sandboxes(
    guest_provider, sandbox
) -> None:
    """One sandbox's files must not appear in another's.

    Volume names are derived, so a derivation that collapses two ids to one
    name would hand a user someone else's workspace — and it would look like
    ordinary reuse, not like a leak.
    """

    provider, instance, *_ = sandbox
    secret = f"private-{uuid4().hex}"

    async def chunks():
        yield secret.encode()

    await provider.write_file(
        instance,
        path="/workspace/private.txt",
        data=chunks(),
        expected_sha256=None,
        deadline_at=_deadline(120),
    )

    other_id = uuid4()
    other_name = naming.container_name(other_id, SandboxKind.WORKSPACE, 1)
    other = await guest_provider.create(
        ProviderCreateSpec(
            sandbox_id=other_id,
            kind=SandboxKind.WORKSPACE,
            epoch=1,
            name=other_name,
            image=os.environ["WORKSPACE_IMAGE"],
            profile_name="workspace",
            profile_digest="sha256:" + "a" * 64,
            deadline_at=_deadline(),
            volume_name=naming.volume_name(other_id, 1),
        )
    )
    try:
        other = await guest_provider.wait_ready(other, deadline_at=_deadline())
        children = await guest_provider.list_files(
            other, path="/workspace", deadline_at=_deadline(120)
        )
        assert not any(entry.path.endswith("private.txt") for entry in children), (
            "a fresh sandbox must not see another workspace's files"
        )
    finally:
        await guest_provider.destroy(other_name, deadline_at=_deadline(120))
        await guest_provider.purge_storage(
            LemmaLocalSandboxProvider._guest_id(other_id, SandboxKind.WORKSPACE),
            deadline_at=_deadline(120),
        )
