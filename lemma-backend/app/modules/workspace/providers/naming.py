"""Deterministic resource names, which is what makes create idempotent.

The name is derived entirely from durable state, so retrying a create either
creates the object or finds it already present. That single property is what
removes the need for a create-attempt ledger and a reconciler to repair
creates whose outcome was never learned.

The epoch in a container name is also the fence. An operation issued against
epoch 4 names a container that no longer exists once the sandbox has moved to
epoch 5, so it fails definitively instead of landing on the replacement. This
is why the epoch must be in the *name* rather than only in a label: a label
would have to be read to be checked, and by then the operation has already
been routed.

Volumes are the exception and deliberately so. A volume name may not be
derived, because the volume that exists today was named `ab-ws-{token}` from a
random token in a database that is being retired. Deriving would silently
strand a user's files, so volumes are looked up by label and adopted, and only
named by this module when there is genuinely nothing to adopt.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.workspace.domain.sandbox import SandboxKind

# Docker names must match [a-zA-Z0-9][a-zA-Z0-9_.-]+ and Kubernetes is stricter
# still, so hex uuids and a single separator keep this portable across the
# providers this seam is meant to grow.
_PREFIX = "lemma"


def container_name(sandbox_id: UUID, kind: SandboxKind, epoch: int) -> str:
    """The fenced name for one sandbox instance.

    >>> container_name(UUID(int=1), SandboxKind.WORKSPACE, 3)
    'lemma-ws-00000000000000000000000000000001-3'
    """
    if epoch < 1:
        raise ValueError("sandbox epoch must be positive")
    return f"{_PREFIX}-{_kind_tag(kind)}-{sandbox_id.hex}-{epoch}"


def volume_name(sandbox_id: UUID, storage_generation: int) -> str:
    """The name for a *newly created* volume.

    Never used to locate an existing one -- see the module docstring.
    """
    if storage_generation < 1:
        raise ValueError("storage generation must be positive")
    return f"{_PREFIX}-vol-{sandbox_id.hex}-{storage_generation}"


def parse_volume_name(name: str) -> tuple[UUID, int] | None:
    """Recover ``(sandbox_id, storage_generation)`` from a volume name.

    Returns None for a name this module did not mint -- including the
    pre-cutover volumes whose names embed a random token. Those are still
    identifiable by label, but not by generation, and a sweep must not treat
    "cannot parse" as "generation zero" or it would delete a live disk.

    >>> parse_volume_name('lemma-vol-00000000000000000000000000000001-2')
    (UUID('00000000-0000-0000-0000-000000000001'), 2)
    """
    parts = name.lstrip("/").split("-")
    if len(parts) != 4 or parts[0] != _PREFIX or parts[1] != "vol":
        return None
    try:
        return UUID(hex=parts[2]), int(parts[3])
    except ValueError:
        return None


def parse_container_name(name: str) -> tuple[UUID, SandboxKind, int] | None:
    """Recover identity from a name, or None if this is not one of ours.

    The sweep relies on this: an object whose name does not parse and whose
    labels do not match is not ours to destroy.
    """
    cleaned = name.lstrip("/")
    parts = cleaned.split("-")
    if len(parts) != 4 or parts[0] != _PREFIX:
        return None
    _, tag, raw_id, raw_epoch = parts
    kind = _kind_from_tag(tag)
    if kind is None:
        return None
    try:
        return UUID(hex=raw_id), kind, int(raw_epoch)
    except ValueError:
        return None


def _kind_tag(kind: SandboxKind) -> str:
    return "ws" if kind is SandboxKind.WORKSPACE else "fn"


def _kind_from_tag(tag: str) -> SandboxKind | None:
    if tag == "ws":
        return SandboxKind.WORKSPACE
    if tag == "fn":
        return SandboxKind.FUNCTION
    return None
