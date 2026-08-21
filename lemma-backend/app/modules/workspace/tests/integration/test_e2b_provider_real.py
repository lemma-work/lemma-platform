"""The E2B provider against the real service.

The unit tests prove the provider against a fake built from the SDK's
signatures. A fake only proves what its author believed, and the belief that
mattered most here -- that a paused sandbox keeps its filesystem -- is the one
production depends on. So it is checked here for real.

Safety, because the account is shared with production: these tests run the
provider under its own metadata namespace, so its queries cannot match a
production sandbox and its sweep cannot see one. Legacy adoption is switched
off for the same reason -- a test must never adopt a real user's workspace.
Every sandbox created is killed on the way out; nothing pre-existing is paused,
killed, or otherwise disturbed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from sandbox_runtime.protocol import (
    ByteRange,
    EnvironmentVariable,
    StartProcessRequest,
)

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    ProviderCreateSpec,
    ProviderStorageKind,
)
from app.modules.workspace.providers.e2b import (
    E2BProviderConfig,
    E2BSandboxProvider,
)
from app.modules.workspace.testing.fake_output_buffer import InMemoryOutputBuffer

pytestmark = [pytest.mark.integration, pytest.mark.provider, pytest.mark.asyncio]

_KEY = os.getenv("E2B_API_KEY", "")
_WORKSPACE_TEMPLATE = os.getenv("E2B_WORKSPACE_TEMPLATE", "")
# Deliberately not the production namespace.
_NAMESPACE = os.getenv("E2B_TEST_METADATA_NAMESPACE", "lemma-conformance")


def _deadline(seconds: int = 120) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest_asyncio.fixture
async def provider() -> AsyncIterator[E2BSandboxProvider]:
    if not _KEY or not _WORKSPACE_TEMPLATE:
        pytest.skip(
            "set E2B_API_KEY and E2B_WORKSPACE_TEMPLATE to run E2B conformance tests"
        )
    instance = E2BSandboxProvider(
        E2BProviderConfig(
            api_key=_KEY,
            workspace_template=_WORKSPACE_TEMPLATE,
            function_template=_WORKSPACE_TEMPLATE,
            sandbox_timeout_seconds=180,
            metadata_namespace=_NAMESPACE,
        ),
        output=InMemoryOutputBuffer(),
    )
    created: list[str] = []
    instance._conformance_created = created  # type: ignore[attr-defined]
    try:
        yield instance
    finally:
        # Only ever sandboxes this test made.
        for provider_id in created:
            try:
                sandbox = await instance._connect(provider_id)
                await sandbox.kill(**instance._api())
            except Exception:
                continue


def _spec(provider: E2BSandboxProvider, sandbox_id, *, epoch: int = 1):
    return ProviderCreateSpec(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=epoch,
        name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, epoch),
        image="",
        profile_name="workspace",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=_deadline(),
    )


async def _create(provider: E2BSandboxProvider, sandbox_id, *, epoch: int = 1):
    instance = await provider.create(_spec(provider, sandbox_id, epoch=epoch))
    provider._conformance_created.append(instance.provider_id)  # type: ignore[attr-defined]
    return instance


async def test_a_real_sandbox_runs_a_command(provider: E2BSandboxProvider) -> None:
    instance = await _create(provider, uuid4())
    await provider.wait_ready(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command="echo hello-from-e2b",
            argv=None,
            cwd="/workspace",
            environment=(EnvironmentVariable(name="LEMMA_TEST", value="1"),),
            tty=None,
            output_limit_bytes=64 * 1024,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )

    snapshot = await provider.read_process_output(
        instance,
        process_id=process_id,
        after_sequence=0,
        wait_seconds=10,
        deadline_at=_deadline(),
    )
    output = b"".join(chunk.data for chunk in snapshot.chunks)
    assert b"hello-from-e2b" in output, output


async def test_real_files_round_trip(provider: E2BSandboxProvider) -> None:
    instance = await _create(provider, uuid4())
    await provider.wait_ready(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    async def payload():
        yield b"written-by-conformance"

    stat = await provider.write_file(
        instance,
        path="/workspace/conformance.txt",
        data=payload(),
        expected_sha256=None,
        deadline_at=_deadline(),
    )
    assert stat.path.endswith("conformance.txt")

    chunks = [
        chunk
        async for chunk in provider.open_file(
            instance,
            path="/workspace/conformance.txt",
            byte_range=ByteRange(offset=0, length=None),
            deadline_at=_deadline(),
        )
    ]
    assert b"".join(chunks) == b"written-by-conformance"

    listed = await provider.list_files(
        instance, path="/workspace", deadline_at=_deadline()
    )
    assert any(entry.path.endswith("conformance.txt") for entry in listed)


async def test_a_real_paused_sandbox_keeps_its_files_and_is_adopted(
    provider: E2BSandboxProvider,
) -> None:
    """The assumption production rests on, and the reason E2B storage is
    modelled as sandbox-native rather than volume-backed.

    Release must pause rather than destroy, and the next create must adopt the
    same sandbox -- because creating a second one would leave the user's files
    in the first with nothing pointing at it.
    """
    sandbox_id = uuid4()
    first = await _create(provider, sandbox_id)
    await provider.wait_ready(
        first, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    async def payload():
        yield b"must-survive-a-pause"

    await provider.write_file(
        first,
        path="/workspace/keep.txt",
        data=payload(),
        expected_sha256=None,
        deadline_at=_deadline(),
    )

    await provider.release(first, kind=SandboxKind.WORKSPACE, deadline_at=_deadline())

    # A later epoch must still adopt: identity decides, not the epoch.
    resumed = await provider.create(_spec(provider, sandbox_id, epoch=2))
    assert resumed.provider_id == first.provider_id
    assert resumed.storage_adopted is True

    await provider.wait_ready(
        resumed, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    chunks = [
        chunk
        async for chunk in provider.open_file(
            resumed,
            path="/workspace/keep.txt",
            byte_range=ByteRange(offset=0, length=None),
            deadline_at=_deadline(),
        )
    ]
    assert b"".join(chunks) == b"must-survive-a-pause"


async def test_real_metadata_queries_isolate_identities(
    provider: E2BSandboxProvider,
) -> None:
    """The whole identity scheme rests on E2B's metadata filter being exact.
    If it were prefix or fuzzy, one user's ensure could adopt another's
    sandbox."""
    mine = uuid4()
    instance = await _create(provider, mine)

    found = await provider.inspect(
        naming.container_name(mine, SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )
    assert found is not None and found.provider_id == instance.provider_id

    assert (
        await provider.inspect(
            naming.container_name(uuid4(), SandboxKind.WORKSPACE, 1),
            deadline_at=_deadline(),
        )
        is None
    )


async def test_creating_twice_really_yields_one_sandbox(
    provider: E2BSandboxProvider,
) -> None:
    sandbox_id = uuid4()
    first = await _create(provider, sandbox_id)
    second = await provider.create(_spec(provider, sandbox_id))

    assert first.provider_id == second.provider_id


async def test_a_first_contact_herd_all_get_served(
    provider: E2BSandboxProvider,
) -> None:
    """Parallel first tool calls on a cold sandbox must not fail.

    This is the 2026-08-20 incident's shape: an agent's first turn fires
    several tool calls at once, each one ensures the sandbox, and the cold
    provision used to fail readiness while E2B was still coming up -- so the
    first call died and only the retry passed. One sandbox is created here
    and then readied and used by a herd, the way the service's singleflight
    hands one provisioning to every waiting caller.
    """
    import asyncio

    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)

    async def tool_call(marker: bytes) -> bytes:
        await provider.wait_ready(
            instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
        )

        async def payload():
            yield marker

        path = f"/workspace/herd-{marker.decode()}.txt"
        await provider.write_file(
            instance,
            path=path,
            data=payload(),
            expected_sha256=None,
            deadline_at=_deadline(),
        )
        chunks = [
            chunk
            async for chunk in provider.open_file(
                instance,
                path=path,
                byte_range=ByteRange(offset=0, length=None),
                deadline_at=_deadline(),
            )
        ]
        return b"".join(chunks)

    markers = [f"call{i}".encode() for i in range(8)]
    results = await asyncio.gather(*(tool_call(marker) for marker in markers))

    assert results == markers
    # The herd shared one sandbox -- nobody got a second one underneath it.
    found = await provider.inspect(
        naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )
    assert found is not None and found.provider_id == instance.provider_id


async def test_files_survive_a_pause_and_a_herd_after_resume(
    provider: E2BSandboxProvider,
) -> None:
    """Pause, resume, and the disk is the same disk -- under parallel load.

    The disk-continuity promise: a paused workspace keeps its filesystem,
    resumes as the same sandbox, and the tool calls waiting on that resume
    all see the files that were written before it.
    """
    import asyncio

    sandbox_id = uuid4()
    first = await _create(provider, sandbox_id)

    async def payload():
        yield b"written-before-the-pause"

    await provider.write_file(
        first,
        path="/workspace/before.txt",
        data=payload(),
        expected_sha256=None,
        deadline_at=_deadline(),
    )
    await provider.release(first, kind=SandboxKind.WORKSPACE, deadline_at=_deadline())

    resumed = await provider.create(_spec(provider, sandbox_id, epoch=2))
    assert resumed.provider_id == first.provider_id
    assert resumed.storage_adopted is True

    async def read_back() -> bytes:
        await provider.wait_ready(
            resumed, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
        )
        chunks = [
            chunk
            async for chunk in provider.open_file(
                resumed,
                path="/workspace/before.txt",
                byte_range=ByteRange(offset=0, length=None),
                deadline_at=_deadline(),
            )
        ]
        return b"".join(chunks)

    results = await asyncio.gather(*(read_back() for _ in range(8)))

    assert results == [b"written-before-the-pause"] * 8


async def test_real_storage_kind_is_sandbox_native(
    provider: E2BSandboxProvider,
) -> None:
    assert provider.storage_kind is ProviderStorageKind.SANDBOX_NATIVE
    assert (
        await provider.find_volume(sandbox_id=uuid4(), deadline_at=_deadline()) is None
    )


async def test_the_sweep_reads_real_metadata_without_claiming_strangers(
    provider: E2BSandboxProvider,
) -> None:
    """Read-only against a production account.

    Every object reported must carry this platform's own metadata key. A sweep
    that claimed sandboxes labelled by anything else would, on this account,
    be destroying live production workspaces.
    """
    sandbox_id = uuid4()
    await _create(provider, sandbox_id)

    objects = await provider.list_objects(deadline_at=_deadline())

    assert all(obj.sandbox_id is not None for obj in objects)
    assert sandbox_id in {obj.sandbox_id for obj in objects}


async def test_a_real_published_port_resolves(provider: E2BSandboxProvider) -> None:
    instance = await _create(provider, uuid4())
    url = await provider.port_base_url(instance, port=8080, deadline_at=_deadline())
    assert url.startswith("https://8080-")


async def test_real_python_keeps_state_across_executions(
    provider: E2BSandboxProvider,
) -> None:
    """E2B has no resident interpreter, so continuity comes from restoring and
    re-saving the namespace. That is only worth claiming if it actually works."""
    from sandbox_runtime.protocol import ExecutePythonRequest

    instance = await _create(provider, uuid4())
    await provider.wait_ready(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    session = _session_ref(uuid4())

    first = await provider.execute_python(
        instance,
        session,
        ExecutePythonRequest(
            operation_id=uuid4(),
            code="value = 6 * 7",
            environment=(),
            output_limit_bytes=64 * 1024,
            deadline_at=_deadline(),
        ),
    )
    assert first.stderr == "" or "Traceback" not in first.stderr, first.stderr

    second = await provider.execute_python(
        instance,
        session,
        ExecutePythonRequest(
            operation_id=uuid4(),
            code="print(value)",
            environment=(),
            output_limit_bytes=64 * 1024,
            deadline_at=_deadline(),
        ),
    )
    assert "42" in second.stdout, (second.stdout, second.stderr)


def _session_ref(session_id):
    """Build the SDK-neutral session reference the provider only reads an id from."""
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Ref:
        session_id: object

    return _Ref(session_id=session_id)
