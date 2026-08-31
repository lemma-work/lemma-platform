"""An agent's mailbox — or a new pod's assistant's — over its whole life.

The provisioning itself lives in
``agent_surfaces.services.email_surface_provisioning``, which every caller now
goes through — notification delivery lazily, the surfaces API and the bundle
applier with their own name and config, and these two eagerly. Teardown goes
through ``AgentSurfaceService.delete_surfaces_for_agent``.

These wrappers exist for one reason: the agent and pod modules must not import
``agent_surfaces``, so the call is made from ``composition`` with lazy imports —
same rule, and same shape, as ``workflow_notifications.py``.

Best-effort in both directions. Creating an agent or a pod must not fail because
a mail domain is unset or Resend is unreachable: both are still perfectly usable
over chat and in the app, and an address can be added later. Deleting an agent
must not fail because a provider will not take the webhook back, either — the
row goes regardless, so a deleted agent never keeps a live mailbox.
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


async def teardown_agent_surfaces(uow, *, pod_id: UUID, agent_id: UUID) -> int:
    """Delete the surfaces belonging to an agent being deleted.

    ``agent_surfaces.agent_id`` is ``ON DELETE SET NULL``, so leaving them
    behind does not orphan them — it turns them into agentless surfaces, which
    is what the pod assistant's own mailbox is. The pod then has two and starts
    answering from a deleted agent's address.

    Returns how many went, for the caller that wants to say so.

    Best-effort per surface — a provider that will not take its webhook back
    must not keep an agent undeletable, and the row goes either way. Not
    best-effort about the database: if the surfaces cannot even be listed, that
    propagates and aborts the deletion, because reporting an agent deleted while
    its mailbox is still receiving is the state this exists to prevent.
    """
    from app.modules.agent_surfaces.api.dependencies import get_surface_service

    return await get_surface_service(uow).delete_surfaces_for_agent(pod_id, agent_id)


async def release_pod_inbound_addresses(uow, *, pod_id: UUID) -> int:
    """Delete a deleted pod's email surfaces, freeing their addresses now.

    `delete_pod` frees the pod's org-unique *name* immediately, so recreating a
    pod under it before the pod-deleted event is consumed races the teardown:
    either the new pod takes a suffixed address and the readable one is
    orphaned, or it inherits an address the deleted pod's correspondents are
    still writing to.

    Email only, so this stays bounded — a Resend surface receives on a
    catch-all webhook and has no provider call to make on the way out. The
    pod-deleted event still tears down everything else.
    """
    from app.modules.agent_surfaces.api.dependencies import get_surface_service

    return await get_surface_service(uow).delete_email_surfaces_for_pod(pod_id)


async def _pod_name(uow, pod_id: UUID) -> str | None:
    """The pod's name, for the readable half of an address.

    Delegates rather than repeating the lookup: ``agent_surfaces`` owns the one
    copy, and this module reaching for ``PodRepository`` itself is how there
    came to be two.
    """
    from app.modules.agent_surfaces.services.pod_name_lookup import pod_name_for

    return await pod_name_for(uow, pod_id)


__all__ = [
    "provision_agent_email_surface",
    "provision_pod_assistant_email_surface",
    "release_pod_inbound_addresses",
    "teardown_agent_surfaces",
]
