"""Pod facts for callers that hold no unit of work.

`pod/contracts/agent_access.py` answers the same question -- which organization
holds this pod -- and is the one to use wherever a `uow` is already open. This
module exists because two callers have none: `workspace` assembles a sandbox's
environment from a service that was never given one, and `function`'s dispatcher
resolves the same scope from inside a job, between units of work. Both used to
open a raw SQLAlchemy session inline; the session belongs in pod, not in them.

Kept as a separate submodule rather than folded in beside the `uow` version so
the import site says which of the two a caller picked, and why -- and named
apart from it, because both call sites import the bare name and the same name
taking a different number of arguments is a trap rather than a namespace.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
model layer, and everything importing any pod contract would otherwise pay for
it.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.pod.infrastructure.pod_reads import resolve_pod_organization_id


async def pod_organization_id_detached(pod_id: UUID) -> UUID | None:
    """The organization holding this pod, read on a short-lived session."""
    return await resolve_pod_organization_id(pod_id)


__all__ = ["pod_organization_id_detached"]
