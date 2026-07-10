"""Identity and pod adapters used to mint scoped workspace environments."""

from uuid import UUID

from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)
from app.modules.pod.infrastructure.pod_reads import resolve_pod_organization_id


async def mint_workspace_token(
    *,
    user_id: UUID,
    workload_type: str | None,
    workload_id: UUID | None,
    pod_id: UUID | None,
    session_id: str,
    workload_name: str | None,
    scope: list[str] | None,
    delegated_tokens_enabled: bool,
) -> str:
    claims = None
    if (
        delegated_tokens_enabled
        and workload_type
        and workload_id is not None
        and pod_id is not None
    ):
        claims = build_delegation_claims(
            workload_type=workload_type,
            workload_id=workload_id,
            pod_id=pod_id,
            session_id=session_id,
            invoked_by_user_id=user_id,
            workload_name=workload_name,
            scope=scope,
        )
    return await get_user_token(user_id, delegation_claims=claims)


async def resolve_workspace_organization_id(pod_id: UUID | None) -> str | None:
    if pod_id is None:
        return None
    organization_id = await resolve_pod_organization_id(pod_id)
    return str(organization_id) if organization_id else None
