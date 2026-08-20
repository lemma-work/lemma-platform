"""Vocabulary shared by the E2B lifecycle and ops halves.

Metadata keys are namespaced rather than hardcoded because one E2B account is
shared: a conformance run must be able to label its sandboxes so that neither
its queries nor its sweeps can ever see production's, and vice versa.
"""

from __future__ import annotations

from contextlib import contextmanager

from sandbox_runtime.errors import (
    SandboxPathNotFound,
    SandboxUnavailable,
)
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers.base import (
    ProviderFailed,
    ProviderGone,
    ProviderRejected,
)

DEFAULT_METADATA_NAMESPACE = "lemma"


def meta_sandbox_id(namespace: str) -> str:
    return f"{namespace}-sandbox-id"


def meta_sandbox_kind(namespace: str) -> str:
    return f"{namespace}-sandbox-kind"


def meta_epoch(namespace: str) -> str:
    return f"{namespace}-epoch"


def meta_profile_digest(namespace: str) -> str:
    return f"{namespace}-profile-digest"


def meta_template(namespace: str) -> str:
    """Which template this sandbox was actually built from.

    Recorded because it is the only honest answer to "is this workspace running
    the image we published?". The profile digest cannot answer it: it is a
    hand-maintained environment variable, and while it sat unchanged at its
    default every sandbox in the fleet stayed pinned to whatever template it was
    first created on -- including through four template releases that were
    supposed to fix the workspaces that were failing.
    """
    return f"{namespace}-template"


META_SANDBOX_ID = meta_sandbox_id(DEFAULT_METADATA_NAMESPACE)
META_SANDBOX_KIND = meta_sandbox_kind(DEFAULT_METADATA_NAMESPACE)
META_EPOCH = meta_epoch(DEFAULT_METADATA_NAMESPACE)
META_PROFILE_DIGEST = meta_profile_digest(DEFAULT_METADATA_NAMESPACE)
META_TEMPLATE = meta_template(DEFAULT_METADATA_NAMESPACE)



def classify(exc: Exception) -> Exception:
    """Turn an SDK failure into this module's two-axis vocabulary.

    Deliberately no retry loop. Whether waiting could help is a question about
    the caller's deadline, which the service owns; the provider's job is to say
    what happened.
    """
    name = type(exc).__name__
    message = str(exc)

    if "NotFound" in name or "not found" in message.lower():
        return ProviderGone(message)
    if "RateLimit" in name or "429" in message:
        return SandboxUnavailable(message, retry_after_ms=2000)
    if "Timeout" in name or "timeout" in message.lower():
        return SandboxUnavailable(message, retry_after_ms=1000)
    if "Authentication" in name or "401" in message or "403" in message:
        return ProviderRejected(f"e2b rejected the credentials: {message}")
    if "Invalid" in name or "400" in message:
        return ProviderRejected(message)
    # Unknown failures are treated as worth retrying: E2B is a network service,
    # and a permanent failure will simply fail again with the same message.
    return SandboxUnavailable(message, retry_after_ms=1000)


def classify_path(exc: Exception, path: str) -> Exception:
    name = type(exc).__name__
    if "NotFound" in name or "not found" in str(exc).lower():
        return SandboxPathNotFound(f"{path} does not exist")
    return classify(exc)


@contextmanager
def sdk_errors(path: str | None = None):
    """Translate whatever one E2B SDK call raises into this module's vocabulary.

    The catch is deliberately broad, and it is broad in exactly one place. The
    SDK's exception classes cannot be imported without the optional extra
    installed, so failures are classified by shape rather than caught by type
    -- and doing that at every call site meant thirty copies of the same three
    lines, each of which could quietly drift.

    Pass `path` for filesystem calls, where "not found" is a missing file
    rather than a missing sandbox.
    """

    try:
        yield
    except Exception as exc:
        raise (classify_path(exc, path) if path is not None else classify(exc)) from exc


@contextmanager
def sdk_best_effort(path: str | None = None):
    """A call whose failure is already the outcome it was asking for.

    Killing a process that has exited, or deleting a file that is not there,
    has achieved what it set out to do. Classifying first and then catching
    only this module's own types keeps that intent narrow: a `NameError` in our
    own code still propagates, where the bare `except Exception` these replaced
    would have swallowed it and reported success.
    """

    try:
        with sdk_errors(path):
            yield
    except (
        ProviderGone,
        ProviderRejected,
        SandboxPathNotFound,
        SandboxUnavailable,
    ):
        return


async def every_page(paginator) -> list:
    """Drain a paginated listing.

    Both listings here read `next_items()` once and treated it as the whole
    answer. The sweep is where that bites: its query is filtered only by kind,
    so it matches the whole fleet, and one page of it was all orphan reclamation
    ever considered -- everything past that was never reclaimed and billed for
    as long as it existed. The account this was found in held 249 sandboxes.

    Adoption's query names a single sandbox id and normally matches one result,
    so it was far less exposed; it is drained here because a listing that can
    paginate should be read as one, not because a specific failure is known.
    """
    found: list = []
    while paginator.has_next:
        found.extend(await paginator.next_items())
    return found


async def ensure_runtime_serving(
    sandbox,
    provider_id: str,
    *,
    runtime_port: int,
    budget_seconds: float = 20.0,
) -> None:
    """Raise unless the runtime inside this sandbox is answering.

    `is_running()` asks E2B about the *VM*, and a VM outlives the process it was
    started for. That gap is the whole 2026-08-16 P0: a function sandbox whose
    runtime had died still reported `running`, so adoption kept accepting it and
    every dispatch got 502 for 100 minutes. Measured against the service:

        healthy runtime   ->  404  (route absent, port listening)
        dead runtime      ->  502  (nothing behind E2B's edge)

    So any HTTP answer at all means the runtime is there -- 404 included, and
    deliberately, because the probe must not depend on an authenticated route.
    A 502 that *persists* means it is not.

    The same 502 is also the normal shape of the first moments after a create
    or a resume: E2B's edge answers before it has routed to the sandbox. That
    is why the probe polls through 5xx rather than taking the first answer as
    final -- treating the first 502 as definitive failed the whole provision
    during ordinary edge lag, and with it the first tool call of every run
    that landed on a cold sandbox (2026-08-20). Only callers off the warm path
    reach here, so the budget can be generous; it only elapses when the
    runtime is *not* answering -- a healthy one replies well inside it.
    """
    with sdk_errors():
        if not await sandbox.is_running():
            raise ProviderFailed(f"e2b sandbox {provider_id} is not running")

    # Substitutable like `query_type` and `pty_size_type` on the SDK seam: a fake
    # sandbox serves no HTTP, and a unit test still has to be able to say whether
    # the runtime inside it is answering.
    substitute = getattr(sandbox, "runtime_status", None)
    if substitute is not None:
        status = substitute(runtime_port)
    else:
        status = await _poll_runtime(
            f"https://{sandbox.get_host(runtime_port)}/health", budget_seconds
        )
    if status is None:
        raise ProviderFailed(
            f"e2b sandbox {provider_id} runtime port {runtime_port} never "
            f"answered within {budget_seconds}s"
        )
    if status >= 500:
        raise ProviderFailed(
            f"e2b sandbox {provider_id} is running but its runtime answered "
            f"{status}; the process inside has died"
        )


async def ensure_serving(
    sandbox,
    provider_id: str,
    *,
    kind: SandboxKind,
    runtime_port: int,
    budget_seconds: float = 20.0,
) -> None:
    """Raise unless the thing this kind's operations depend on answers.

    A function sandbox's operations go through its runtime HTTP server, and a
    VM outlives the process it was started for -- the port probe is what
    catches that. A workspace starts no such server: the edge answers 502 for
    its profile port forever, so its readiness is one command through the
    sandbox agent instead. Splitting by kind is what `sandbox-protocol.md`
    §8 specifies.
    """
    if kind is SandboxKind.FUNCTION:
        await ensure_runtime_serving(
            sandbox,
            provider_id,
            runtime_port=runtime_port,
            budget_seconds=budget_seconds,
        )
    else:
        await ensure_agent_serving(
            sandbox, provider_id, budget_seconds=budget_seconds
        )


async def ensure_agent_serving(
    sandbox,
    provider_id: str,
    *,
    budget_seconds: float = 20.0,
) -> None:
    """Raise unless this sandbox's agent answers a trivial command.

    A workspace has no runtime HTTP server for the port probe to ask: the
    template starts no listener on the profile port, and E2B's edge answers
    502 for an unlistened port forever. So the probe that catches a dead
    *function* runtime failed a healthy *workspace* every single time --
    verified against the real service, where a fresh workspace's port
    answered 502 for 45 straight seconds while its agent replied in half
    a second. Every fresh workspace's first ensure was rejected, and the
    retry only passed because adoption skips the probe. What a workspace's
    operations actually need is the sandbox agent (envd), which every
    command and filesystem call goes through, and the honest check that it
    is serving is one command run through it -- `sandbox-protocol.md` §8:
    "E2B workspace: exact sandbox connected plus one native
    command/filesystem smoke operation".
    """
    with sdk_errors():
        if not await sandbox.is_running():
            raise ProviderFailed(f"e2b sandbox {provider_id} is not running")

    # Substitutable like `runtime_status`: a fake sandbox runs no commands,
    # and a unit test still has to be able to say whether the agent answered.
    substitute = getattr(sandbox, "smoke_command", None)
    runner = substitute if substitute is not None else lambda: _run_true(sandbox)
    if not await _poll_agent(runner, budget_seconds):
        raise ProviderFailed(
            f"e2b sandbox {provider_id} is running but its agent never "
            f"answered a command within {budget_seconds}s"
        )


async def _run_true(sandbox) -> bool:
    """One trivial command through the sandbox agent: did it answer?"""
    try:
        with sdk_errors():
            result = await sandbox.commands.run("true")
    except (ProviderGone, SandboxUnavailable):
        # Not up yet: the agent can lag the VM by a moment after a create or
        # a resume, and the budget decides how long "a moment" is allowed to
        # be. A definitive refusal still escapes.
        return False
    return getattr(result, "exit_code", None) == 0


async def _poll_agent(runner, budget_seconds: float) -> bool:
    """True when the smoke command answers inside the budget."""
    import asyncio
    import time

    deadline = time.monotonic() + budget_seconds
    while True:
        if await runner():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.25)


async def _poll_runtime(url: str, budget_seconds: float) -> int | None:
    """The runtime's status, or None if it never answered inside the budget.

    Both failure shapes are polled through, because both are transient on a
    sandbox that is still coming up: a transport error is the edge not yet
    listening for this host at all, and a 5xx is the edge listening but not
    yet routed to the sandbox. Only an answer below 500 ends the wait early --
    it is the one outcome that cannot mean "not up yet". A budget that expires
    on 5xx answers returns the last one, so the failure still says *what* the
    runtime answered rather than merely that it did not.
    """
    import asyncio
    import time

    import httpx

    deadline = time.monotonic() + budget_seconds
    status: int | None = None
    async with httpx.AsyncClient(timeout=budget_seconds) as client:
        while True:
            try:
                status = (await client.get(url)).status_code
                if status < 500:
                    return status
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                return status
            await asyncio.sleep(0.1)
