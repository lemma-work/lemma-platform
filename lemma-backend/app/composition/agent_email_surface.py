"""Giving a new agent its own mailbox, at the moment it is created.

The provisioning itself lives in
``agent_surfaces.services.email_surface_provisioning`` — notification delivery
provisions lazily through the same function, and two implementations is how the
readable address and the ``pod-{hex}`` one came to coexist.

This wrapper exists for one reason: the agent module must not import
``agent_surfaces``, so the call is made from ``composition`` with lazy imports —
same rule, and same shape, as ``workflow_notifications.py``.

Best-effort. Creating an agent must not fail because a mail domain is unset or
Resend is unreachable: the agent is still perfectly usable over chat and in the
app, and an address can be added later.
"""

from __future__ import annotations

from uuid import UUID


async def provision_agent_email_surface(
    uow, *, pod_id: UUID, agent_id: UUID, agent_name: str
) -> str | None:
    """Create this agent's Resend surface, returning its address.

    None when email is not configured for the deployment, or when provisioning
    failed — both are survivable, and both are logged rather than raised.
    """
    from app.modules.agent_surfaces.api.dependencies import get_surface_service
    from app.modules.agent_surfaces.services.email_surface_provisioning import (
        provision_email_surface,
    )

    surface, _ = await provision_email_surface(
        get_surface_service(uow),
        uow.session,
        pod_id=pod_id,
        agent_id=agent_id,
        agent_name=agent_name,
        pod_name=await _pod_name(uow, pod_id),
    )
    return surface.surface_identity_email if surface else None


async def _pod_name(uow, pod_id: UUID) -> str | None:
    from app.modules.pod.infrastructure.pod_repositories import PodRepository

    pod = await PodRepository(uow).get(pod_id)
    return getattr(pod, "name", None)


__all__ = ["provision_agent_email_surface"]
