"""Which sandbox gets the loopback relay to the owner's Mac.

On Desktop the installation owner's agent can run commands on the Mac (host
execution, docs/architecture/desktop-host-execution.md), so the dev server it
starts listens on the Mac's own `127.0.0.1`. The browser that checks the
result runs in the owner's workspace sandbox in the guest VM. The loopback
relay (docs/architecture/desktop-security.md, "The loopback relay") is how
that browser reaches the Mac's loopback, and this decides who has it.

Exactly one kind of sandbox does: a **workspace** belonging to the
**installation owner**, on a **Desktop** install. That is where the owner's
browser runs. A function sandbox has no browser; an invited person's
workspace shares the owner's computer but not their trust, and must never be
able to reach the servers on it.

Asked each time a sandbox is provisioned, and the answer is fixed into the
container: a running sandbox keeps what it was created with.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from app.modules.workspace.domain.sandbox import (
    Sandbox,
    SandboxKind,
    SandboxOwnerKind,
)


def _is_desktop() -> bool:
    # Imported here, as `provider_factory` does, so the workspace module's
    # import graph does not grow identity's for every deployment that is not
    # Desktop.
    from app.modules.identity.contracts.installation import is_desktop_installation

    return is_desktop_installation()


async def _is_owner(user_id: UUID) -> bool:
    from app.modules.identity.contracts.installation import is_installation_owner

    return await is_installation_owner(user_id)


@dataclass(frozen=True, slots=True)
class InstallationFacts:
    """Where the two answers come from: identity's, injected so a test can
    state them."""

    is_desktop: Callable[[], bool] = _is_desktop
    is_owner: Callable[[UUID], Awaitable[bool]] = _is_owner


FACTS = InstallationFacts()


async def no_host_loopback(sandbox: Sandbox) -> bool:
    """The policy where there is no owner's Mac to reach: nobody gets the relay.

    `SandboxService`'s default, so every deployment but Desktop -- and every
    test that builds a service directly -- grants nothing.
    """
    return False


async def is_owner_browser_sandbox(
    sandbox: Sandbox, *, facts: InstallationFacts = FACTS
) -> bool:
    """Whether this sandbox is the owner's own workspace on a Desktop install.

    A failure to read the owner is not absorbed as "no": it fails the
    provision, as any other database failure during it does.
    """
    if sandbox.kind is not SandboxKind.WORKSPACE:
        return False
    if sandbox.owner_kind is not SandboxOwnerKind.USER:
        return False
    if not facts.is_desktop():
        return False
    return await facts.is_owner(sandbox.owner_id)
