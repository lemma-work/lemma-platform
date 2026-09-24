"""Host sandboxes: a workspace whose commands run on the owner's Mac.

See docs/architecture/desktop-host-execution.md. A host sandbox is an ordinary
sandbox row -- same lifecycle, same state machine -- whose compute is a folder
on the installation owner's computer, reached through their Agent Host.

**The choice is in the id.** A host sandbox's id is a UUIDv8 carrying a fixed
tag, derived from the conversation it serves. Nothing else this platform mints
is a v8 (workspaces are user ids, function sandboxes pod ids, both v4 or v7),
so which fabric a sandbox runs on is readable from the id alone, cannot change
for the life of the sandbox, and needs no lookup on the hot path. That is the
contract's "recorded on the sandbox": an operation on a host sandbox can only
ever go to the host, and one on any other sandbox never can.

**One per conversation.** The root is per conversation (§5), and the owner's
ordinary VM workspace -- where their browser lives -- keeps its own id. A run
that executes on the host therefore uses two sandboxes: this one for commands
and files, and the VM one for the browser.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

#: The provider name recorded on a host sandbox's instances.
HOST_EXECUTION_PROVIDER = "agent_host"

#: The first six bytes of every host sandbox id.
_TAG = b"lmhost"


def host_sandbox_id(conversation_id: UUID) -> UUID:
    """The host sandbox a conversation executes in. Deterministic."""
    digest = hashlib.sha256(b"lemma-host-execution:" + conversation_id.bytes)
    raw = bytearray(_TAG + digest.digest()[: 16 - len(_TAG)])
    raw[6] = (raw[6] & 0x0F) | 0x80  # version 8
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 9562 variant
    return UUID(bytes=bytes(raw))


def is_host_sandbox_id(sandbox_id: UUID) -> bool:
    return sandbox_id.version == 8 and sandbox_id.bytes[: len(_TAG)] == _TAG


def host_sandbox_slug(conversation_id: UUID) -> str:
    """The row's slug, unique per owner, so the row is found as well as made."""
    return f"host-{conversation_id.hex}"


@dataclass(frozen=True, slots=True)
class HostBinding:
    """Which host a host sandbox runs on, and how its root is chosen.

    ``root_hint`` is the folder the conversation is bound to, if Lemma knows
    one; otherwise the host makes ``~/lemma/c/<day>/<slug>``. ``root`` is what
    the host answered ``workspace.open`` with, once it has.
    """

    sandbox_id: UUID
    host_id: UUID
    owner_id: UUID
    conversation_id: UUID
    slug: str
    day: str
    root_hint: str | None = None
    root: str | None = None


@dataclass(frozen=True, slots=True)
class HostWorkspace:
    """A host sandbox that is open, as a run's tools need it."""

    sandbox_id: UUID
    root: str
