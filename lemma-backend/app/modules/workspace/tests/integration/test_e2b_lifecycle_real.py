"""The E2B lifecycle, end to end, against the real service and a real Redis.

`test_e2b_provider_real.py` covers the provider's contract. This covers the
*lifecycle* -- the sequence a workspace actually lives through, in order:
created, used, paused, resumed, used again, drifted, replaced, destroyed -- and
it does so through the real Redis-backed output buffer rather than the in-memory
one.

That last part is the point. Every real-E2B test before this substituted
`InMemoryOutputBuffer`, so the one component that carries a command's output
between the callback that receives it and the poll that reads it was faked in
every test that touched the real service. Three separate production failures
lived in exactly that seam, and none of them could have been caught.

The failures this file is built from, all observed in production:

* A workspace ran for days on the template it was first created with, through
  four releases meant to fix it, because adoption compared only a hand-edited
  profile digest. 249 sandboxes across four old templates; zero on the
  configured one.
* A pause preserved resident memory because the SDK default was never
  overridden, so a leaked browser was snapshotted and restored on every resume,
  and the sandbox's memory exhaustion became permanent.
* A wedged sandbox started processes that never printed and never exited, and
  every layer reported success, so agents polled a corpse until someone killed
  the sandbox by hand.

Safety, because the account is shared with production: everything here runs
under its own metadata namespace, so no query can match a production sandbox and
no sweep can see one. Only sandboxes these tests created are ever killed.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from sandbox_runtime.protocol import (
    EnvironmentVariable,
    ProcessState,
    StartProcessRequest,
)

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import ProviderCreateSpec
from app.modules.workspace.providers.e2b import E2BProviderConfig, E2BSandboxProvider
from app.modules.workspace.providers.e2b_output import E2BOutputBuffer

pytestmark = [pytest.mark.integration, pytest.mark.provider, pytest.mark.asyncio]

_KEY = os.getenv("E2B_API_KEY", "")
_TEMPLATE = os.getenv("E2B_WORKSPACE_TEMPLATE", "")
# Deliberately not the production namespace.
_NAMESPACE = os.getenv("E2B_TEST_METADATA_NAMESPACE", "lemma-lifecycle")

# What a healthy sandbox costs, measured against the real service: `echo` came
# back in 0.36s and `lemma --version` in 0.98s, warm or resumed. These bounds
# are several times that, because the failure they guard against is not a slow
# command -- it is a command that returns nothing for thirty seconds.
_TRIVIAL_COMMAND_BUDGET_SECONDS = 10.0
_RESUME_BUDGET_SECONDS = 30.0

_TERMINAL = {
    ProcessState.SUCCEEDED,
    ProcessState.FAILED,
    ProcessState.CANCELLED,
    ProcessState.TIMED_OUT,
}


def _deadline(seconds: int = 120) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest_asyncio.fixture
async def provider() -> AsyncIterator[E2BSandboxProvider]:
    if not _KEY or not _TEMPLATE:
        pytest.skip("set E2B_API_KEY and E2B_WORKSPACE_TEMPLATE to run this suite")
    instance = _build_provider(_TEMPLATE)
    created: list[str] = []
    instance._lifecycle_created = created  # type: ignore[attr-defined]
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


def _build_provider(template: str) -> E2BSandboxProvider:
    return E2BSandboxProvider(
        E2BProviderConfig(
            api_key=_KEY,
            workspace_template=template,
            function_template=template,
            sandbox_timeout_seconds=600,
            metadata_namespace=_NAMESPACE,
        ),
        # The real one. Faking this is what hid every bug in the seam it owns.
        output=E2BOutputBuffer(key_prefix=f"workspace:e2b:lifecycle:{uuid4().hex}"),
    )


def _spec(sandbox_id: UUID, *, epoch: int = 1, digest: str | None = None):
    return ProviderCreateSpec(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=epoch,
        name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, epoch),
        image="",
        profile_name="workspace",
        profile_digest=digest or ("sha256:" + "a" * 64),
        deadline_at=_deadline(),
    )


async def _create(provider: E2BSandboxProvider, sandbox_id: UUID, *, epoch: int = 1):
    instance = await provider.create(_spec(sandbox_id, epoch=epoch))
    provider._lifecycle_created.append(instance.provider_id)  # type: ignore[attr-defined]
    return instance


async def _run(
    provider: E2BSandboxProvider,
    instance,
    command: str,
    *,
    wait_seconds: float = 20.0,
) -> tuple[float, bytes, int | None, ProcessState]:
    """Start a command and collect it the way a tool call does.

    Returns the wall time as well as the output, because "did it come back" and
    "did it come back before the agent gave up" are different questions and only
    the second one was ever failing.
    """
    started = time.monotonic()
    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command=command,
            argv=None,
            cwd="/workspace",
            environment=(EnvironmentVariable(name="LEMMA_TEST", value="1"),),
            tty=None,
            output_limit_bytes=256 * 1024,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )
    collected = bytearray()
    after = 0
    state = ProcessState.RUNNING
    exit_code: int | None = None
    while time.monotonic() - started < wait_seconds:
        snapshot = await provider.read_process_output(
            instance,
            process_id=process_id,
            after_sequence=after,
            wait_seconds=min(5.0, wait_seconds),
            deadline_at=_deadline(),
        )
        state = snapshot.state
        exit_code = snapshot.exit_code
        for chunk in snapshot.chunks:
            after = max(after, chunk.sequence)
            collected.extend(chunk.data)
        if state in _TERMINAL:
            break
    return time.monotonic() - started, bytes(collected), exit_code, state


# ---------------------------------------------------------------------------
# The straight line: create, run, pause, resume, run again
# ---------------------------------------------------------------------------


async def test_a_new_sandbox_runs_a_command_and_reports_its_exit(
    provider: E2BSandboxProvider,
) -> None:
    instance = await _create(provider, uuid4())
    await provider.wait_ready(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    elapsed, output, exit_code, state = await _run(provider, instance, "echo alive")

    assert b"alive" in output, output
    assert exit_code == 0
    assert state is ProcessState.SUCCEEDED
    assert elapsed < _TRIVIAL_COMMAND_BUDGET_SECONDS, (
        f"echo took {elapsed:.1f}s; a command that returns nothing for this long "
        "is what agents read as a hang"
    )


async def test_a_failing_command_reports_its_exit_code_not_just_silence(
    provider: E2BSandboxProvider,
) -> None:
    """A non-zero exit is a result. The SDK raises for it, and a swallowed raise
    is indistinguishable from a process that never finished."""
    instance = await _create(provider, uuid4())

    _, output, exit_code, state = await _run(
        provider, instance, "echo to-stderr >&2; exit 3"
    )

    assert exit_code == 3, (exit_code, output)
    assert state is ProcessState.FAILED
    assert b"to-stderr" in output, output


async def test_a_paused_sandbox_keeps_its_files_and_still_runs_commands(
    provider: E2BSandboxProvider,
) -> None:
    """The property production depends on, checked as a sequence rather than as
    two separate facts: the disk survives *and* the resumed sandbox still
    works."""
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)
    await _run(provider, instance, "echo persisted > /workspace/marker.txt")

    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    started = time.monotonic()
    resumed = await provider.create(_spec(sandbox_id, epoch=2))
    await provider.wait_ready(
        resumed, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    resume_seconds = time.monotonic() - started

    assert resumed.provider_id == instance.provider_id, (
        "a second sandbox would strand the user's files in the first"
    )
    assert resumed.storage_adopted is True
    assert resume_seconds < _RESUME_BUDGET_SECONDS, (
        f"resume took {resume_seconds:.1f}s and every tool call pays it"
    )

    elapsed, output, exit_code, _ = await _run(
        provider, instance, "cat /workspace/marker.txt"
    )
    assert b"persisted" in output, output
    assert exit_code == 0
    assert elapsed < _TRIVIAL_COMMAND_BUDGET_SECONDS, (
        f"the first command after a resume took {elapsed:.1f}s -- the resumed "
        "sandbox is the one that was failing in production, so this is the "
        "measurement that matters"
    )


async def test_a_pause_does_not_carry_running_processes_across(
    provider: E2BSandboxProvider,
) -> None:
    """A filesystem-only pause is what the architecture documents promise, and
    what makes a leaked browser survivable: it dies with the pause instead of
    being snapshotted and restored on every resume for the life of the sandbox.
    """
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)
    # `pgrep -f` matches the whole command line, so the shell running the count
    # matches its own pattern. The bracket makes the pattern not match itself,
    # which is the standard way out and the reason this is not just `pgrep -x`:
    # that caps the process *name* at 15 characters and silently finds nothing.
    marker = f"/tmp/lemma-mark-{uuid4().hex[:8]}.sh"
    pattern = f"[{marker[5]}]{marker[6:]}"
    await _run(
        provider,
        instance,
        f"printf 'sleep 3600\\n' > {marker} && chmod +x {marker} && "
        f"setsid nohup {marker} >/dev/null 2>&1 < /dev/null & echo started",
    )
    await asyncio.sleep(1.0)
    # `pgrep -c` prints 0 *and* exits non-zero when nothing matches, so an `||`
    # fallback would print the count twice.
    _, before, _, _ = await _run(provider, instance, f"pgrep -c -f '{pattern}' || true")
    # More than one can match -- `setsid` and `nohup` both carry the script
    # path in their command line. Only "something was running" matters here.
    assert int(before.strip() or 0) >= 1, (
        "the marker process did not start",
        before,
    )

    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    resumed = await provider.create(_spec(sandbox_id, epoch=2))
    await provider.wait_ready(
        resumed, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )

    _, after, _, _ = await _run(provider, resumed, f"pgrep -c -f '{pattern}' || true")
    assert after.strip().startswith(b"0"), (
        "a process survived the pause, so `keep_memory` is back on its SDK "
        f"default and a leaked browser is now permanent: {after!r}"
    )


# ---------------------------------------------------------------------------
# Streaming: the seam the in-memory buffer was hiding
# ---------------------------------------------------------------------------


async def test_output_arrives_incrementally_rather_than_all_at_the_end(
    provider: E2BSandboxProvider,
) -> None:
    """An agent watching a build needs the first line before the last one."""
    instance = await _create(provider, uuid4())
    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command="echo first; sleep 3; echo second",
            argv=None,
            cwd="/workspace",
            environment=(),
            tty=None,
            output_limit_bytes=64 * 1024,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )

    early = await provider.read_process_output(
        instance,
        process_id=process_id,
        after_sequence=0,
        wait_seconds=8.0,
        deadline_at=_deadline(),
    )
    early_output = b"".join(chunk.data for chunk in early.chunks)

    assert b"first" in early_output, early_output
    assert b"second" not in early_output, (
        "the poll waited for the whole command instead of returning what was "
        "already printed"
    )
    assert early.state not in _TERMINAL


async def test_a_finished_command_returns_immediately_not_at_the_window(
    provider: E2BSandboxProvider,
) -> None:
    """The regression from #379, held against the real service.

    A command that has already exited must not cost the caller the poll window.
    Asserting on elapsed time rather than on content is the whole point: the
    output was always correct, and the tool call still took thirty seconds.
    """
    instance = await _create(provider, uuid4())
    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command="printf done",
            argv=None,
            cwd="/workspace",
            environment=(),
            tty=None,
            output_limit_bytes=1024,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )
    # Let it finish and let the watcher record the exit, so the first read below
    # is the one an agent's poll actually makes.
    await asyncio.sleep(2.0)

    started = time.monotonic()
    snapshot = await provider.read_process_output(
        instance,
        process_id=process_id,
        after_sequence=0,
        wait_seconds=30.0,
        deadline_at=_deadline(),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, (
        f"polling a finished command blocked for {elapsed:.1f}s of a 30s window"
    )
    assert b"done" in b"".join(chunk.data for chunk in snapshot.chunks)


async def test_a_silent_running_command_still_reports_as_running(
    provider: E2BSandboxProvider,
) -> None:
    """The other side of the same coin: waking early on an exit must not turn a
    quiet build into a finished one."""
    instance = await _create(provider, uuid4())
    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command="sleep 20",
            argv=None,
            cwd="/workspace",
            environment=(),
            tty=None,
            output_limit_bytes=1024,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )

    snapshot = await provider.read_process_output(
        instance,
        process_id=process_id,
        after_sequence=0,
        wait_seconds=3.0,
        deadline_at=_deadline(),
    )

    assert snapshot.state not in _TERMINAL
    assert snapshot.exit_code is None
    assert snapshot.chunks == ()


async def test_a_killed_process_reaches_a_terminal_state(
    provider: E2BSandboxProvider,
) -> None:
    """Agents kill processes exactly when a tool call looks stuck. A kill that
    leaves the process reading as running pins the sandbox as busy for the hour
    the buffer retains it, so the idle sweep can never reclaim it -- the sandbox
    that just frustrated someone becomes the one that never gets cleaned up."""
    instance = await _create(provider, uuid4())
    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command="sleep 300",
            argv=None,
            cwd="/workspace",
            environment=(),
            tty=None,
            output_limit_bytes=1024,
            deadline_at=_deadline(),
            initial_input=None,
        ),
        deadline_at=_deadline(),
    )
    await asyncio.sleep(1.0)

    await provider.terminate_process(
        instance,
        process_id=process_id,
        grace_seconds=0.0,
        deadline_at=_deadline(),
    )

    deadline = time.monotonic() + 20
    state = ProcessState.RUNNING
    while time.monotonic() < deadline:
        snapshot = await provider.read_process_output(
            instance,
            process_id=process_id,
            after_sequence=0,
            wait_seconds=2.0,
            deadline_at=_deadline(),
        )
        state = snapshot.state
        if state in _TERMINAL:
            break
    assert state in _TERMINAL, f"a killed process still reads as {state}"


# ---------------------------------------------------------------------------
# Replacement: the fence that has to fire for a fix to reach anyone
# ---------------------------------------------------------------------------


async def test_a_sandbox_on_a_different_template_is_replaced_not_adopted(
    provider: E2BSandboxProvider,
) -> None:
    """Against the real service, because this is the one that failed silently.

    Adoption ignored the template entirely, so publishing a new one changed
    nothing for anybody who already had a workspace. The fleet sat on four old
    templates and the configured one had zero sandboxes -- through four releases
    that were each meant to fix the workspaces that were failing.
    """
    sandbox_id = uuid4()
    first = await _create(provider, sandbox_id)

    # Same account, same identity, a different configured template. Nothing else
    # changes -- which is exactly the deploy that used to be a no-op.
    moved = _build_provider(f"{_TEMPLATE}-nonexistent-successor")
    moved._lifecycle_created = provider._lifecycle_created  # type: ignore[attr-defined]

    with pytest.raises(Exception):
        # The successor template does not exist, so provisioning must fail --
        # but the stale sandbox must already be gone, because leaving it would
        # mean a later lookup could still land on it.
        await moved.create(_spec(sandbox_id, epoch=2))

    found = await provider.inspect(
        naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )
    assert found is None or found.provider_id != first.provider_id, (
        "the sandbox on the old template survived, so the new template will "
        "never reach this workspace"
    )


async def test_an_adopted_sandbox_keeps_reporting_the_template_it_runs(
    provider: E2BSandboxProvider,
) -> None:
    """The stamp is what makes drift detectable at all. Without it every
    sandbox reads as "unknown", and the fleet's staleness is invisible -- which
    is how this went unnoticed for months."""
    sandbox_id = uuid4()
    await _create(provider, sandbox_id)

    found = await provider.inspect(
        naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )

    assert found is not None
    assert found.template == _TEMPLATE, (
        f"created on {_TEMPLATE} but reports {found.template!r}"
    )


# ---------------------------------------------------------------------------
# Destruction
# ---------------------------------------------------------------------------


async def test_a_destroyed_sandbox_is_actually_gone(
    provider: E2BSandboxProvider,
) -> None:
    """A destroy that returns is not a destroy that happened. The orphan sweep
    logged eighteen reclaims every five minutes for hours -- the same eighteen
    ids, eleven times each -- while the account held ninety-nine paused
    sandboxes and the count never moved."""
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)
    name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1)

    await provider.destroy(name, deadline_at=_deadline())

    assert await provider.inspect(name, deadline_at=_deadline()) is None, (
        f"{instance.provider_id} is still listed after destroy"
    )


async def test_destroying_a_sandbox_that_is_already_gone_is_not_an_error(
    provider: E2BSandboxProvider,
) -> None:
    """The sweep runs on a schedule against a listing that can be stale, so this
    race is the normal case, not an exceptional one."""
    sandbox_id = uuid4()
    await _create(provider, sandbox_id)
    name = naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1)

    await provider.destroy(name, deadline_at=_deadline())
    await provider.destroy(name, deadline_at=_deadline())


async def test_a_real_sandbox_is_created_to_pause_on_timeout_not_die(
    provider: E2BSandboxProvider,
) -> None:
    """Against the real service, because it is unfixable if we get it wrong.

    A lifecycle is chosen at create and E2B offers no way to change it
    afterwards -- there is no `set_lifecycle`, and the connect endpoint carries
    only a timeout. So every sandbox made while this is wrong stays wrong for as
    long as it exists, and what "wrong" means here is that E2B deletes it, and
    the user's files with it, the moment its timeout elapses.

    That was the default. This asserts against the state E2B itself reports,
    rather than against the argument we passed, because the argument being
    accepted is not evidence that it was honoured.
    """
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)

    info = await provider._sdk.get_info(instance.provider_id, **provider._api())
    lifecycle = getattr(info, "lifecycle", None) or {}

    assert lifecycle.get("on_timeout") == "pause", (
        f"E2B will kill this sandbox, and its disk, on timeout: {lifecycle!r}"
    )
