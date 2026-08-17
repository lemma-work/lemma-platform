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
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import ProviderCreateSpec
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
