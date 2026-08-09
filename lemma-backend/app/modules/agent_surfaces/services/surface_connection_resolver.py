"""Resolves the pod-visible identity of the account each surface runs on.

A surface is a pod row; the account behind it is personal
(``accounts.user_id``), listed only to its owner. That split is right — nobody
else should hold someone's token — but it left a hole: an editor could see that
an agent was live on Telegram and not who had connected it, so a bot that went
dark had no owner anyone could name.

This closes the hole from the read side only. It joins identity (label, owner,
health) onto the surface list and never touches credentials: ``SurfaceAccountSummary``
carries no secret, and the whole page resolves in two queries regardless of how
many surfaces the pod has.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger
from app.modules.agent_surfaces.api.schemas import (
    SurfaceConnection,
    SurfaceConnectionOwner,
    SurfaceConnectionStatus,
)
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.ports import (
    SurfaceAccountPort,
    SurfaceConnectionOwnerPort,
)

logger = get_logger(__name__)


def _connection_status(raw: str | None) -> SurfaceConnectionStatus:
    """An unrecognized account status reads as CONNECTED rather than raising:
    this is a display field, and a status added elsewhere must not 500 a list."""
    try:
        return SurfaceConnectionStatus(str(raw or "").upper())
    except ValueError:
        return SurfaceConnectionStatus.CONNECTED


class SurfaceConnectionResolver:
    def __init__(
        self,
        *,
        account_port: SurfaceAccountPort,
        owner_port: SurfaceConnectionOwnerPort,
    ):
        self._account_port = account_port
        self._owner_port = owner_port

    async def for_surfaces(
        self,
        surfaces: Sequence[AgentSurfaceEntity],
        *,
        pod_id: UUID,
        viewer_user_id: UUID | None = None,
    ) -> dict[UUID, SurfaceConnection]:
        """Connections keyed by surface id. Surfaces with no account (a
        SYSTEM-credential surface runs on Lemma's own bot) are simply absent.

        Best-effort: this is an annotation on a read, so a database hiccup here
        drops the annotation rather than the surfaces it annotates."""
        account_ids = [
            surface.account_id for surface in surfaces if surface.account_id is not None
        ]
        if not account_ids:
            return {}

        try:
            accounts = await self._account_port.list_account_summaries(account_ids)
            owners = await self._owner_port.list_pod_owners(
                [account.user_id for account in accounts.values()], pod_id=pod_id
            )
        except SQLAlchemyError:
            logger.debug(
                "agent_surfaces.surface_connection_resolver.connection_lookup_failed.diagnostic",
                pod_id=str(pod_id),
            )
            return {}

        connections: dict[UUID, SurfaceConnection] = {}
        for surface in surfaces:
            if surface.account_id is None:
                continue
            account = accounts.get(surface.account_id)
            if account is None:
                # The id outlived the row. Say so rather than dropping the block:
                # "the account this ran on is gone" is the actionable reading.
                connections[surface.id] = SurfaceConnection(
                    account_id=surface.account_id,
                    connector_id="",
                    status=SurfaceConnectionStatus.MISSING,
                )
                continue

            owner = owners.get(account.user_id)
            connections[surface.id] = SurfaceConnection(
                account_id=account.id,
                connector_id=account.connector_id,
                display_name=account.display_name or account.email,
                status=_connection_status(account.status),
                connected_by=SurfaceConnectionOwner(
                    user_id=account.user_id,
                    name=owner.name if owner else None,
                    email=owner.email if owner else None,
                    is_pod_member=bool(owner and owner.is_pod_member),
                    is_you=viewer_user_id is not None
                    and account.user_id == viewer_user_id,
                ),
            )
        return connections

    async def for_surface(
        self,
        surface: AgentSurfaceEntity,
        *,
        pod_id: UUID,
        viewer_user_id: UUID | None = None,
    ) -> SurfaceConnection | None:
        """Single-surface convenience, with the same best-effort contract."""
        resolved = await self.for_surfaces(
            [surface], pod_id=pod_id, viewer_user_id=viewer_user_id
        )
        return resolved.get(surface.id)
