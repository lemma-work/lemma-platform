""" "Share as <slug>": one of my pod accounts, run by a SHARED connector.

Two acts that used to be two requests -- widen the account to the pod, then
point a SHARED connector at it or declare one -- and so could stop halfway: the
account shared with everyone, and the connector step refused (the slug was a
per-member connector's, or the owner may not manage connectors). Here every
check either step makes is asked first, and both steps run in the request's
one unit of work, so a refusal anywhere leaves the account as it was.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    PodConnectorCreateRequest,
    PodConnectorResponse,
    PodConnectorUpdateRequest,
)
from app.modules.connectors.domain.pod_connector import (
    PodAccountVisibility,
    PodConnectorMode,
)
from app.modules.connectors.domain.pod_connector_errors import (
    PodAccountNotFoundError,
    PodConnectorValidationError,
)
from app.modules.connectors.services.pod_connector_service import PodConnectorService


async def share_account_as(
    service: PodConnectorService,
    ctx: Context,
    pod_id: UUID,
    pod_account_id: UUID,
    *,
    slug: str,
    purpose: str | None = None,
) -> PodConnectorResponse:
    pod_account = await service.pod_accounts.get(pod_account_id)
    if pod_account is None or pod_account.pod_id != pod_id:
        raise PodAccountNotFoundError()
    # Sharing is the owner's alone; the connector half is an editor's.
    await ctx.require(
        Permissions.CONNECTOR_ACCOUNT_SHARE,
        ResourceRef.connector_account(pod_id, pod_account.id),
    )
    await ctx.require(Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id))
    held = await service.pod_connectors.get_by_slug(pod_id, slug)
    if held is not None:
        if held.mode is not PodConnectorMode.SHARED:
            raise PodConnectorValidationError(
                f"{slug} uses each person's own account, so it cannot run as "
                "one shared account.",
                reason="not_shared_mode",
            )
        if held.auth_config_id != pod_account.auth_config_id:
            raise PodConnectorValidationError(
                f"{slug} is on a different installation.",
                reason="installation_mismatch",
            )
    if pod_account.visibility is PodAccountVisibility.PERSONAL:
        await service.update_account(
            ctx,
            pod_id,
            pod_account.id,
            visibility=PodAccountVisibility.POD,
            share_with=None,
            is_member_default=None,
        )
    if held is not None:
        return await service.update_connector(
            ctx,
            pod_id,
            slug,
            PodConnectorUpdateRequest(shared_pod_account_id=pod_account.id),
        )
    return await service.create_connector(
        ctx,
        pod_id,
        PodConnectorCreateRequest(
            slug=slug,
            auth_config_id=pod_account.auth_config_id,
            mode=PodConnectorMode.SHARED,
            shared_pod_account_id=pod_account.id,
            purpose=purpose,
        ),
    )


__all__ = ["share_account_as"]
