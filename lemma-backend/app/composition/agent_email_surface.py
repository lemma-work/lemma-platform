"""Giving a new agent — or a new pod's assistant — its own mailbox at creation.

The provisioning itself lives in
``agent_surfaces.services.email_surface_provisioning``, which every caller now
goes through — notification delivery lazily, the surfaces API and the bundle
applier with their own name and config, and these two eagerly.

These wrappers exist for one reason: the agent and pod modules must not import
``agent_surfaces``, so the call is made from ``composition`` with lazy imports —
same rule, and same shape, as ``workflow_notifications.py``.

Best-effort. Creating an agent or a pod must not fail because a mail domain is
unset or Resend is unreachable: both are still perfectly usable over chat and in
the app, and an address can be added later.
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


async def provision_pod_assistant_email_surface(
    uow, *, pod_id: UUID, pod_name: str | None
) -> str | None:
    """Create the pod assistant's mailbox, returning its address.

    Both ``agent_id`` and ``agent_name`` are None. That is not "unset": a surface
    with no agent of its own is exactly what ``surfaces_for_agent`` looks for on
    the assistant's behalf, and a None name is what makes the address the pod's
    own — ``acme@`` rather than ``pod-default.acme@``.

    The pod's name is passed in rather than looked up. The caller is pod creation
    and already holds it, and the row a lookup would read is the one it just
    wrote.

    Best-effort, on the same terms as an agent's: None when email is not
    configured or provisioning failed, and both are survivable.
    """
    from app.modules.agent_surfaces.api.dependencies import get_surface_service
    from app.modules.agent_surfaces.services.email_surface_provisioning import (
        provision_email_surface,
    )

    surface, _ = await provision_email_surface(
        get_surface_service(uow),
        uow.session,
        pod_id=pod_id,
        agent_id=None,
        agent_name=None,
        pod_name=pod_name,
    )
    return surface.surface_identity_email if surface else None


async def _pod_name(uow, pod_id: UUID) -> str | None:
    from app.modules.pod.infrastructure.pod_repositories import PodRepository

    pod = await PodRepository(uow).get(pod_id)
    return getattr(pod, "name", None)


__all__ = [
    "provision_agent_email_surface",
    "provision_pod_assistant_email_surface",
]
