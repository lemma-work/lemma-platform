"""Host sandboxes: a workspace whose commands run on the user's Mac.

See docs/architecture/desktop-host-execution.md. A host sandbox is an ordinary
sandbox row -- same lifecycle, same state machine -- whose compute is a folder
on the computer of the user a run is for, reached through the Agent Host
paired to them.

**The choice is in the id.** A host sandbox's id is a UUIDv8 carrying a fixed
tag, derived from the conversation it serves. Nothing else this platform mints
is a v8 (workspaces are user ids, function sandboxes pod ids, both v4 or v7),
so which fabric a sandbox runs on is readable from the id alone, cannot change
for the life of the sandbox, and needs no lookup on the hot path. That is the
contract's "recorded on the sandbox": an operation on a host sandbox can only
ever go to the host, and one on any other sandbox never can.

**One per conversation.** The root is per conversation (§5), and the user's
ordinary VM workspace -- where their browser lives -- keeps its own id. A run
that executes on the host therefore uses two sandboxes: this one for commands
and files, and the VM one for the browser.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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


def conversation_of_host_sandbox_slug(slug: str) -> UUID | None:
    """The conversation a host sandbox row serves, read back from its slug."""
    prefix = "host-"
    if not slug.startswith(prefix):
        return None
    try:
        return UUID(hex=slug[len(prefix) :])
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class HostTarget:
    """Where a host sandbox's operations go. Nothing stores this; see
    ``services/host_workspace.SqlHostTargets``.

    ``root`` is the folder the conversation's last host run opened, when one
    has; it is sent back as the hint on a re-open.
    """

    host_id: UUID
    conversation_id: UUID
    root: str | None = None


@dataclass(frozen=True, slots=True)
class HostFolder:
    """What names a conversation's default folder, ``~/lemma/c/<day>/<slug>``.

    ``root_hint`` is the folder the conversation is bound to, if Lemma knows
    one. The Mac decides (§5) and remembers what it decided, per conversation.
    """

    day: str
    slug: str
    root_hint: str | None = None


def workspace_open_params(
    conversation_id: UUID, folder: HostFolder | None, *, root_hint: str | None
) -> dict[str, object]:
    """``workspace.open``'s params (§4). ``date`` is the conversation's own
    day, not today's, so a first open on another day finds the same folder."""
    params: dict[str, object] = {
        "root_hint": root_hint,
        "grants": [],
        "conversation_id": str(conversation_id),
    }
    if folder is not None:
        params["date"] = folder.day
        params["slug"] = folder.slug
    return params


@dataclass(frozen=True, slots=True)
class HostWorkspace:
    """A host sandbox that is open, as a run's tools need it.

    ``host_id`` is the Mac the run recorded when it chose the host; the run's
    operations go there and nowhere else (``run_pinned_host``). None only for
    a workspace just opened, before selection stamps it.
    """

    sandbox_id: UUID
    root: str
    host_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RunHostPin:
    """The host and root one run recorded for its host sandbox."""

    sandbox_id: UUID
    host_id: UUID
    root: str


_RUN_HOST_PIN: ContextVar[RunHostPin | None] = ContextVar(
    "lemma_run_host_pin", default=None
)


@contextmanager
def run_pinned_host(pin: RunHostPin | None) -> Iterator[None]:
    """Route this sandbox's operations, inside the block, by the run's record.

    The sandbox id names only the conversation, and a conversation's runs can
    each have chosen a different Mac. Without a pin an operation goes where
    the conversation's latest host run went, which may be another run's Mac.
    """
    token = _RUN_HOST_PIN.set(pin)
    try:
        yield
    finally:
        _RUN_HOST_PIN.reset(token)


def pinned_host_for(sandbox_id: UUID) -> RunHostPin | None:
    """The calling run's recorded host for ``sandbox_id``, if it pinned one."""
    pin = _RUN_HOST_PIN.get()
    return pin if pin is not None and pin.sandbox_id == sandbox_id else None
