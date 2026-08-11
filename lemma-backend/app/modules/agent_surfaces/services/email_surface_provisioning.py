"""Giving an agent a mailbox — the one place that does it.

There were two. Agent creation minted the readable ``{agent}.{pod}@domain`` with
insert-and-retry, while notification delivery minted ``pod-{hex}@domain`` through
a different function with different failure behaviour. Two schemes meant the
address a person saw depended on which code path happened to create it, and only
one of them was typeable.

Both callers now come here:

- ``create_agent`` provisions eagerly, so an address exists before anyone can
  write to the agent. Nobody can email an agent whose mailbox is conditional on
  it having sent something first.
- Notification delivery provisions lazily, for an agent that turns out to have no
  way to reach anyone — the pod assistant, or an agent that predates per-agent
  mailboxes.

``agent_id=None`` is the pod assistant. That is not "unset": a surface with no
agent of its own is exactly what ``surfaces_for_agent`` looks for on its behalf,
so the pod's own mailbox is what the assistant sends from.
"""

from __future__ import annotations

from uuid import UUID

from httpx import HTTPError
from sqlalchemy.exc import IntegrityError

from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    has_native_credentials,
)
from app.modules.agent_surfaces.services.email_address_allocation import (
    candidate_addresses,
    slugify,
)

logger = get_logger(__name__)


def email_is_configured() -> bool:
    """Whether this deployment can mint a working mailbox at all.

    The same key-and-domain test the surfaces catalog uses to decide whether to
    offer SYSTEM mode, so what the UI offers and what delivery actually does
    cannot drift apart. It replaced a separate on/off setting, which could be —
    and on dev was — true in one process and false in another.
    """
    return has_native_credentials(SurfacePlatform.RESEND)


def surface_name_for(agent_name: str | None) -> str:
    """Surface names are unique per pod, so the platform default collides.

    ``resend`` is taken by the first agent that wants one; every later agent
    needs a name of its own, and the pod's own mailbox keeps the plain default.
    """
    return f"resend-{slugify(agent_name)}" if agent_name else "resend"


async def provision_email_surface(
    service,
    session,
    *,
    pod_id: UUID,
    agent_id: UUID | None,
    agent_name: str | None,
    pod_name: str | None,
) -> AgentSurfaceEntity | None:
    """Create the mailbox and return the surface, or None if we could not.

    Best-effort by construction. Creating an agent must not fail because a mail
    domain is unset, and a notification that cannot be delivered is already a
    handled outcome — the row exists and the inbox has it. What must *not*
    happen is a silent success where the surface exists with an address nobody
    can receive on, which is why an unconfigured deployment returns None rather
    than inventing a domain.
    """
    if not email_is_configured():
        return None

    domain = surface_settings.resend_inbound_domain
    if not domain:  # pragma: no cover - email_is_configured already requires it
        return None

    addresses = candidate_addresses(
        agent_name=agent_name, pod_name=pod_name, domain=domain
    )

    # Insert and retry rather than check-then-insert: the unique index on
    # surface_identity_email is the arbiter, and a pre-check would still race.
    #
    # Each attempt gets a savepoint. This runs inside the caller's transaction,
    # and a unique violation aborts a Postgres transaction outright — so without
    # one, the second attempt raises PendingRollbackError, the commit fails, and
    # *creating an agent* returns 500 because two of them wanted the same
    # address. Same reasoning, and the same shape, as
    # `notification_repository.create`.
    for address in addresses:
        try:
            async with session.begin_nested():
                return await service.create_surface(
                    pod_id=pod_id,
                    platform=SurfacePlatform.RESEND,
                    agent_id=agent_id,
                    name=surface_name_for(agent_name),
                    config=SurfaceConfig(),
                    credential_mode=SurfaceCredentialMode.SYSTEM,
                    surface_identity_email=address,
                )
        except IntegrityError:
            # The address (or the surface name) is taken. Try the next candidate;
            # the savepoint means the outer transaction is still usable.
            continue
        except (AgentSurfaceError, HTTPError, OSError) as exc:
            # Deliberately not a bare ``Exception``. These three are the mail
            # system saying no — a refused domain, Resend unreachable, a name
            # already taken. A ``TypeError`` from our own code is not that, and
            # swallowing it would turn a bug into agents that quietly have no
            # mailbox for as long as nobody looks.
            logger.warning(
                "agent_surfaces.email_surface_provisioning.failed.degraded",
                pod_id=str(pod_id),
                agent_id=str(agent_id) if agent_id else None,
                error=str(exc),
            )
            return None

    logger.warning(
        "agent_surfaces.email_surface_provisioning.address_unavailable.degraded",
        pod_id=str(pod_id),
        agent_id=str(agent_id) if agent_id else None,
    )
    return None


__all__ = [
    "email_is_configured",
    "provision_email_surface",
    "surface_name_for",
]
