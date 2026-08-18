"""A function sandbox outlives its runtime, and the platform must notice.

This is the P0 from the 2026-08-16 QA pass, reproduced against the real service
rather than described. Every function run in development between 17:15 and 18:54
failed -- 13+ consecutive -- from a single trigger: one slow cold start left a
sandbox whose runtime process had died. Deleting it by hand fixed it instantly.

The mechanism only exists on a VM-backed provider, and that is the point of
running this against E2B rather than Docker. On Docker the runtime is tini's
only child, so killing it stops the container, the provider reports it gone, and
adoption replaces it -- Docker self-heals and cannot reproduce this at all. On
E2B the sandbox is a VM: the process dies, the VM keeps running, and
``inspect`` keeps saying ``running`` forever.

Measured here, and matching the QA report's numbers exactly:

    healthy sandbox, port 8090   ->  404   (route absent, port listening)
    runtime killed, port 8090    ->  502   (nothing behind E2B's edge)
    E2B state after the kill     ->  running

404 is what told QA the template was sound. 502 is what the dispatcher saw on
every subsequent run, and 5xx was the one status that did *not* evict the
endpoint -- while a healthy-but-routeless 404 did. Exactly inverted.

Safety: everything runs under its own metadata namespace and only sandboxes
these tests created are ever killed, because the account is shared with
production.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import ProviderCreateSpec, ProviderFailed
from app.modules.workspace.providers.e2b import (
    E2BProviderConfig,
    E2BSandboxProvider,
)
from app.modules.workspace.testing.fake_output_buffer import InMemoryOutputBuffer

pytestmark = [pytest.mark.integration, pytest.mark.provider, pytest.mark.asyncio]

_KEY = os.getenv("E2B_API_KEY", "")
_FUNCTION_TEMPLATE = os.getenv("E2B_FUNCTION_TEMPLATE", "")
# Deliberately not the production namespace.
_NAMESPACE = os.getenv("E2B_TEST_METADATA_NAMESPACE", "lemma-fn-liveness")

#: The port the function runtime serves on, per Dockerfile.function's CMD.
_RUNTIME_PORT = 8090

#: `pkill -f lemma-function-runtime` also matches the shell running it, so the
#: kill command kills itself and the runtime survives. The bracket stops the
#: pattern matching its own command line.
_KILL_RUNTIME = "pkill -f '[l]emma-function-runtime'; echo killed"


def _deadline(seconds: int = 120) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest_asyncio.fixture
async def provider() -> AsyncIterator[E2BSandboxProvider]:
    if not _KEY or not _FUNCTION_TEMPLATE:
        pytest.skip(
            "set E2B_API_KEY and E2B_FUNCTION_TEMPLATE to run the function "
            "liveness suite"
        )
    instance = E2BSandboxProvider(
        E2BProviderConfig(
            api_key=_KEY,
            workspace_template=_FUNCTION_TEMPLATE,
            function_template=_FUNCTION_TEMPLATE,
            sandbox_timeout_seconds=180,
            metadata_namespace=_NAMESPACE,
        ),
        output=InMemoryOutputBuffer(),
    )
    created: list[str] = []
    instance._liveness_created = created  # type: ignore[attr-defined]
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


def _spec(sandbox_id, *, epoch: int = 1) -> ProviderCreateSpec:
    return ProviderCreateSpec(
        sandbox_id=sandbox_id,
        kind=SandboxKind.FUNCTION,
        epoch=epoch,
        name=naming.container_name(sandbox_id, SandboxKind.FUNCTION, epoch),
        image="",
        profile_name="function",
        profile_digest="sha256:" + "b" * 64,
        deadline_at=_deadline(),
    )


async def _create(provider: E2BSandboxProvider, sandbox_id, *, epoch: int = 1):
    instance = await provider.create(_spec(sandbox_id, epoch=epoch))
    provider._liveness_created.append(instance.provider_id)  # type: ignore[attr-defined]
    return instance


async def _runtime_status(provider: E2BSandboxProvider, provider_id: str) -> int | str:
    """What the dispatcher would see on the runtime port."""
    sandbox = await provider._connect(provider_id)
    url = f"https://{sandbox.get_host(_RUNTIME_PORT)}/health"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            return (await client.get(url)).status_code
    except httpx.HTTPError as error:
        return type(error).__name__


async def test_a_killed_runtime_leaves_a_sandbox_the_provider_still_calls_running(
    provider: E2BSandboxProvider,
) -> None:
    """The trap, stated as a test: liveness is not what ``inspect`` reports."""
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)

    healthy = await _runtime_status(provider, instance.provider_id)
    assert healthy == 404, (
        f"expected 404 from a healthy runtime (route absent, port listening); "
        f"got {healthy}. If this is 502 the template itself is broken."
    )

    sdk = await provider._connect(instance.provider_id)
    await sdk.commands.run(_KILL_RUNTIME, timeout=30)
    # The VM keeps running; only the process is gone.
    await asyncio.sleep(3)

    assert "NO_RUNTIME" in (
        await sdk.commands.run(
            "pgrep -af '[l]emma-function-runtime' || echo NO_RUNTIME",
            timeout=30,
        )
    ).stdout

    poisoned = await _runtime_status(provider, instance.provider_id)
    assert poisoned == 502, (
        f"expected 502 once nothing is behind E2B's edge; got {poisoned}"
    )

    # And the part that made this permanent: the provider still says running, so
    # adoption keeps succeeding and keeps handing this sandbox out.
    seen = await provider.inspect(instance.name, deadline_at=_deadline())
    assert seen is not None and seen.running, (
        "the provider reported the sandbox as gone -- if that were true, "
        "adoption would have replaced it and there would have been no outage"
    )


async def test_replacing_the_sandbox_restores_service(
    provider: E2BSandboxProvider,
) -> None:
    """What quarantine does, end to end: destroy, re-create, serve again.

    The dispatcher's quarantine path destroys the sandbox behind a failed
    endpoint precisely so the next ensure cannot adopt it. This asserts the
    property that path depends on -- that a replacement is a *different*
    sandbox and that it serves.
    """
    sandbox_id = uuid4()
    first = await _create(provider, sandbox_id)
    sdk = await provider._connect(first.provider_id)
    await sdk.commands.run(_KILL_RUNTIME, timeout=30)
    await asyncio.sleep(3)
    assert await _runtime_status(provider, first.provider_id) == 502

    await provider.destroy(first.provider_id, deadline_at=_deadline())

    second = await _create(provider, sandbox_id, epoch=2)
    assert second.provider_id != first.provider_id, (
        "re-create returned the same sandbox; adoption would hand back the dead "
        "one and the outage would continue"
    )
    assert await _runtime_status(provider, second.provider_id) == 404


# ---------------------------------------------------------------------------
# Pause and resume: functions keep memory, workspaces do not
# ---------------------------------------------------------------------------
#
# `test_e2b_lifecycle_real.py` asserts the opposite for a WORKSPACE, and both
# assertions have to hold at once. The workspace rule exists because a headed
# Chrome was snapshotted and restored on every idle release until the sandbox
# was permanently exhausted. A function sandbox has no such process to preserve
# -- `lemma-function` runs function code and nothing else -- so the only thing
# the snapshot carries is the runtime a cold start would otherwise rebuild.


async def test_a_function_sandbox_keeps_its_memory_across_pause_and_resume(
    provider: E2BSandboxProvider,
) -> None:
    """The whole point of the change: resume restores, rather than rebuilds."""
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)
    sdk = await provider._connect(instance.provider_id)

    # A process that only resident memory can carry across the pause. Detached,
    # so it is not tied to the command channel that started it.
    tag = f"lemma-memory-marker-{uuid4().hex[:12]}"
    await sdk.commands.run(
        f"setsid nohup sleep 600 --{tag} >/dev/null 2>&1 < /dev/null & echo started",
        timeout=30,
    )
    await asyncio.sleep(1.0)
    before = await sdk.commands.run(f"pgrep -c -f '{tag}' || true", timeout=30)
    assert int((before.stdout or "0").strip() or 0) >= 1, "the marker never started"

    await provider.release(
        instance, kind=SandboxKind.FUNCTION, deadline_at=_deadline()
    )

    resumed_at = time.monotonic()
    resumed = await provider.create(_spec(sandbox_id, epoch=2))
    provider._liveness_created.append(resumed.provider_id)  # type: ignore[attr-defined]
    await provider.wait_ready(
        resumed, kind=SandboxKind.FUNCTION, deadline_at=_deadline()
    )
    resume_ms = (time.monotonic() - resumed_at) * 1000

    resumed_sdk = await provider._connect(resumed.provider_id)
    after = await resumed_sdk.commands.run(f"pgrep -c -f '{tag}' || true", timeout=30)
    survived = int((after.stdout or "0").strip() or 0)

    print(f"\n[bench] function resume-from-pause: {resume_ms:.0f}ms")
    assert survived >= 1, (
        "the marker process did not survive the pause, so `keep_memory` is "
        "still False for functions and resume is still a cold boot"
    )
    # And it is genuinely serving afterwards, not merely restored.
    assert await _runtime_status(provider, resumed.provider_id) == 404


# The workspace counterpart -- that a workspace pause drops resident memory --
# is guarded by `test_a_pause_does_not_carry_running_processes_across` in
# `test_e2b_lifecycle_real.py`, which owns the real `lemma-workspace` template.
# It cannot be asserted from this file: the fixture here points both templates
# at `lemma-function`, so a sandbox created as WORKSPACE would still be running
# the function template and the assertion would mean nothing.


async def test_releasing_a_function_sandbox_leaves_it_serving_when_resumed(
    provider: E2BSandboxProvider,
) -> None:
    """The root cause of the P0, and the reason functions keep memory.

    A filesystem-only pause resumes the sandbox *without its runtime process*.
    Nothing re-runs the image's CMD on resume, so the VM comes back, E2B reports
    it ``running``, adoption accepts it -- and port 8090 answers 502 forever.
    Measured directly against the service:

        keep_memory=True   fresh 404 / runtime 1  ->  resumed 404 / runtime 1
        keep_memory=False  fresh 404 / runtime 1  ->  resumed 502 / runtime 0

    So the 100-minute outage did not need a slow cold start to explain it. Idle
    release pauses an idle function sandbox, the next ensure resumes it, and it
    comes back dead. The quarantine path recovers from that after one failure;
    this is what stops it happening.

    Goes through ``release``/``create`` rather than the SDK, because the point is
    that the provider's own idle-release path is safe now.
    """
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)
    assert await _runtime_status(provider, instance.provider_id) == 404

    await provider.release(
        instance, kind=SandboxKind.FUNCTION, deadline_at=_deadline()
    )
    resumed = await provider.create(_spec(sandbox_id, epoch=2))
    provider._liveness_created.append(resumed.provider_id)  # type: ignore[attr-defined]
    await provider.wait_ready(
        resumed, kind=SandboxKind.FUNCTION, deadline_at=_deadline()
    )

    served = await _runtime_status(provider, resumed.provider_id)
    assert served == 404, (
        f"a released-then-resumed function sandbox answered {served}. 502 means "
        "the runtime process did not come back, which is the poisoned state the "
        "whole P0 was made of -- keep_memory has regressed to filesystem-only "
        "for functions."
    )


async def test_readiness_refuses_a_sandbox_whose_runtime_has_died(
    provider: E2BSandboxProvider,
) -> None:
    """`wait_ready` now asks the runtime, not the VM.

    This is what turns the outage into zero failed runs rather than one. The
    reactive path -- quarantine on a 5xx dispatch -- recovers *after* a run has
    already failed. Readiness refusing the sandbox means adoption never hands it
    out, so the failure never reaches a user.

    It used to check `is_running()` alone, which is true of a VM whose process
    has died, so this sandbox passed readiness for 100 minutes.
    """
    sandbox_id = uuid4()
    instance = await _create(provider, sandbox_id)
    # Healthy first, or the assertion below proves nothing.
    await provider.wait_ready(
        instance, kind=SandboxKind.FUNCTION, deadline_at=_deadline()
    )

    sdk = await provider._connect(instance.provider_id)
    await sdk.commands.run(_KILL_RUNTIME, timeout=30)
    await asyncio.sleep(3)

    with pytest.raises(ProviderFailed) as failure:
        await provider.wait_ready(
            instance, kind=SandboxKind.FUNCTION, deadline_at=_deadline()
        )

    # 502 from E2B's edge is the usual shape; a sandbox that stops answering
    # entirely reads as never-answered. Either is a refusal, which is the claim.
    assert "502" in str(failure.value) or "never answered" in str(failure.value), (
        f"readiness failed for the wrong reason: {failure.value}"
    )
    # Still running, which is exactly why is_running() was not enough.
    seen = await provider.inspect(instance.name, deadline_at=_deadline())
    assert seen is not None and seen.running


async def test_verify_ready_replaces_a_dead_sandbox_before_anyone_dispatches(
    provider: E2BSandboxProvider,
) -> None:
    """`verify_ready=True` is what turns one failed run into none.

    The function resolver passes it on an endpoint-cache miss. Before this it was
    deleted on arrival -- `del ... verify_ready` -- and the client returned
    `ready=True` unconditionally, on reasoning that only holds for a sandbox it
    created rather than adopted.

    Driven through the provider rather than the client because the client needs a
    database; what is asserted is the property the client depends on: readiness
    refuses the dead sandbox, and a replacement serves.
    """
    sandbox_id = uuid4()
    first = await _create(provider, sandbox_id)
    sdk = await provider._connect(first.provider_id)
    await sdk.commands.run(_KILL_RUNTIME, timeout=30)
    await asyncio.sleep(3)

    # What the client now does: verify, and on failure destroy and re-ensure.
    with pytest.raises(ProviderFailed):
        await provider.wait_ready(
            first, kind=SandboxKind.FUNCTION, deadline_at=_deadline()
        )
    await provider.destroy(first.provider_id, deadline_at=_deadline())
    second = await _create(provider, sandbox_id, epoch=2)

    await provider.wait_ready(
        second, kind=SandboxKind.FUNCTION, deadline_at=_deadline()
    )
    assert second.provider_id != first.provider_id
    assert await _runtime_status(provider, second.provider_id) == 404
