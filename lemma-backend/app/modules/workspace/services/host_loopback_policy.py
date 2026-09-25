"""Which sandbox gets the loopback relay to this Mac.

On Desktop a user's agent can run commands on the Mac (host execution,
docs/architecture/desktop-host-execution.md), so the dev server it starts
listens on the Mac's own `127.0.0.1`. The browser that checks the result runs
in that user's workspace sandbox in the guest VM. The loopback relay
(docs/architecture/desktop-security.md, "The loopback relay") is how that
browser reaches the Mac's loopback, and this decides who has it.

There is no installation owner. The relay leads to *this* Mac, so it belongs
to whoever *this Mac's own Agent Host* is paired to -- the host running next
to this backend, not any host that happens to be paired to it. Exactly one
kind of sandbox gets it: a **workspace**, owned by a **person**, on a
**Desktop** install, whose user holds a live pairing with this Mac's Agent
Host.

"This Mac's Agent Host" is read from its own config, which locald points the
backend at (`DESKTOP_AGENT_HOST_CONFIG_PATH`): the host ids of its pairings.
A host id is minted by this backend when a host pairs and handed only to that
host, so another machine -- a teammate's own Mac paired to this Lemma, or an
Agent Host somebody ran inside their own sandbox -- holds a different one and
cannot present this Mac's. A function sandbox has no browser. Anyone else's
workspace shares this computer but not its loopback.

Whether that host is online or has "Run commands on this Mac" switched on is
not part of the grant: both change while a sandbox lives, and the grant is
fixed when the container is made. locald's end of the relay checks the switch
on every connection instead, so the relay carries nothing while it is off and
works as soon as it is turned on, with no container to replace.

Asked each time a sandbox is provisioned, and the answer is fixed into the
container: a running sandbox keeps what it was created with.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.modules.workspace.config import workspace_settings
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


def local_agent_host_ids(path: str | None) -> frozenset[UUID]:
    """The host ids this Mac's Agent Host holds pairings for, from its config.

    Read each time rather than at start: the Agent Host writes its config when
    it pairs, which is usually after this backend started, and re-pairing to
    another account replaces the id. Only `targets[].host_id` is taken; the
    file's secrets are not looked at. A missing, unreadable or malformed file,
    or a target that is not a valid id or is disabled, contributes nothing.
    """
    if not path:
        return frozenset()
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return frozenset()
    targets = document.get("targets") if isinstance(document, dict) else None
    if not isinstance(targets, list):
        return frozenset()
    host_ids: set[UUID] = set()
    for target in targets:
        if not isinstance(target, dict) or target.get("enabled", True) is False:
            continue
        try:
            host_ids.add(UUID(str(target.get("host_id"))))
        except ValueError:
            continue
    return frozenset(host_ids)


def _local_host_ids() -> frozenset[UUID]:
    return local_agent_host_ids(workspace_settings.desktop_agent_host_config_path)


async def _is_paired_to_any_of(user_id: UUID, host_ids: Collection[UUID]) -> bool:
    from app.modules.agent.contracts.host_execution import is_paired_to_any_of

    return await is_paired_to_any_of(user_id, host_ids)


@dataclass(frozen=True, slots=True)
class LocalHostFacts:
    """Where the answers come from, injected so a test can state them."""

    is_desktop: Callable[[], bool] = _is_desktop
    local_host_ids: Callable[[], frozenset[UUID]] = _local_host_ids
    paired_to_any_of: Callable[[UUID, Collection[UUID]], Awaitable[bool]] = (
        _is_paired_to_any_of
    )


FACTS = LocalHostFacts()


async def no_host_loopback(sandbox: Sandbox) -> bool:
    """The policy where there is no Mac to reach: nobody gets the relay.

    `SandboxService`'s default, so every deployment but Desktop -- and every
    test that builds a service directly -- grants nothing.
    """
    return False


async def is_local_host_users_browser_sandbox(
    sandbox: Sandbox, *, facts: LocalHostFacts = FACTS
) -> bool:
    """Whether this sandbox is the workspace of a user this Mac's own Agent
    Host is paired to. See the module.

    A failure to read the host rows is not absorbed as "no": it fails the
    provision, as any other database failure during it does.
    """
    if sandbox.kind is not SandboxKind.WORKSPACE:
        return False
    if sandbox.owner_kind is not SandboxOwnerKind.USER:
        return False
    if not facts.is_desktop():
        return False
    host_ids = facts.local_host_ids()
    if not host_ids:
        return False
    return await facts.paired_to_any_of(sandbox.owner_id, host_ids)
