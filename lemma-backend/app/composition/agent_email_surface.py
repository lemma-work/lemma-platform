"""Giving a new agent its own mailbox.

An agent that can be emailed needs an address before anyone can email it, and
that address has to exist the moment the agent does — someone writing to the
agent is how the first conversation starts, and it cannot be conditional on the
agent having sent something first. So provisioning happens at creation rather
than lazily on first send. The address is already on the surfaces API as
``surface_identity_email``; showing it in the UI is a separate change.

Everything here is best-effort. Creating an agent must not fail because a mail
domain is unset or Resend is unreachable: the agent is still perfectly usable
over chat and in the app, and an address can be added later. The one thing that
must not happen is a *silent* success where the surface exists with an address
nobody can receive on, which is why an unconfigured deployment returns None
rather than inventing a domain.

Lives in ``composition`` because the agent module must not import
``agent_surfaces`` — same rule, and same lazy imports, as
``workflow_notifications.py``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.log.log import get_logger

logger = get_logger(__name__)


async def provision_agent_email_surface(
    uow, *, pod_id: UUID, agent_id: UUID, agent_name: str
) -> str | None:
    """Create this agent's Resend surface, returning its address.

    None when email is not configured for the deployment, or when provisioning
    failed — both are survivable, and both are logged rather than raised.
    """
    from app.modules.agent_surfaces.config import (
        resolve_resend_api_key,
        surface_settings,
    )

    if not (resolve_resend_api_key() and surface_settings.resend_inbound_domain):
        return None

    from app.modules.agent_surfaces.domain.entities import (
        SurfaceConfig,
        SurfaceCredentialMode,
        SurfacePlatform,
    )
    from app.modules.agent_surfaces.services.email_address_allocation import (
        candidate_addresses,
        slugify,
    )

    addresses = candidate_addresses(
        agent_name=agent_name,
        pod_name=await _pod_name(uow, pod_id),
        domain=surface_settings.resend_inbound_domain,
    )

    from app.modules.agent_surfaces.api.dependencies import get_surface_service

    service = get_surface_service(uow)
    # Insert and retry rather than check-then-insert: the unique index on
    # surface_identity_email is the arbiter, and a pre-check would still race.
    #
    # Each attempt gets a savepoint. This runs inside the caller's
    # agent-creation transaction, and a unique violation aborts a Postgres
    # transaction outright — so without one, the second attempt raises
    # PendingRollbackError, the commit fails, and *creating an agent* returns
    # 500 because two of them wanted the same address. Same reasoning, and the
    # same shape, as `notification_repository.create`.
    for address in addresses:
        try:
            async with uow.session.begin_nested():
                surface = await service.create_surface(
                    pod_id=pod_id,
                    platform=SurfacePlatform.RESEND,
                    agent_id=agent_id,
                    # Surface names are unique per pod, so the platform default
                    # ("resend") collides as soon as a second agent wants one.
                    name=f"resend-{slugify(agent_name)}",
                    config=SurfaceConfig(),
                    credential_mode=SurfaceCredentialMode.SYSTEM,
                    surface_identity_email=address,
                )
        except IntegrityError:
            # The address (or the surface name) is taken. Try the next candidate;
            # the savepoint means the outer transaction is still usable.
            continue
        except Exception as exc:  # noqa: BLE001 - see module docstring
            logger.warning(
                "agent_surfaces.agent_email_surface.provision_failed.degraded",
                pod_id=str(pod_id),
                error=str(exc),
            )
            return None
        return surface.surface_identity_email

    logger.warning(
        "agent_surfaces.agent_email_surface.address_unavailable.degraded",
        pod_id=str(pod_id),
    )
    return None


async def _pod_name(uow, pod_id: UUID) -> str | None:
    from app.modules.pod.infrastructure.pod_repositories import PodRepository

    pod = await PodRepository(uow).get(pod_id)
    return getattr(pod, "name", None)


__all__ = ["provision_agent_email_surface"]
