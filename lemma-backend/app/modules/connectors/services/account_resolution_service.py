"""Resolves which connected account a connector call runs against.

Resolution rule:
- **Account owner**: a user (or a workload delegating for them) using their own
  account needs no grant beyond ``connector.use`` — the owner-match
  early-return below handles it.
- **Default pod agent**: mirrors the invoking user, so it resolves to the
  user's own account for the connector.
- **Named workload with a pinned (shared) account id**: authority comes from
  the workload's grants alone — ``connector.use`` on the connector plus
  ``connector_account.use`` on the pinned account — and is therefore
  invoker-independent. This is what makes a shared-sender setup (e.g. one
  team Gmail account pinned on a function) work for every pod member.

Connector-account *visibility* is derived (RESTRICTED iff any grant row
exists); for human actors that still means "non-owners need a user-level
grant". For workloads the derived visibility is inert: the grant-first
evaluation in ``_authorize_delegated_workload`` consults the workload's own
grants for every visibility.
"""

from uuid import UUID

from app.core.authorization.context import ActorType, Context, ResourceRef
from app.core.authorization.current import get_current_context
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
    WorkloadPrincipalType,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.grants import connector_resource_id
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.errors import (
    AccountResolutionError,
    ConnectorAccessDeniedError,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort


class AccountResolutionService:
    def __init__(
        self,
        *,
        account_repository: AccountRepositoryPort,
        authz_read_port: object | None = None,
        authorization_service: object | None = None,
    ):
        self.account_repo = account_repository
        self.authz_read_port = authz_read_port
        self.authorization_service = authorization_service

    async def _get_owned_account(
        self,
        *,
        user_id: UUID,
        connector_id: str,
        account_id: UUID | None,
    ) -> AccountEntity:
        if account_id is not None:
            account = await self._get_account_for_connector(
                account_id=account_id,
                connector_id=connector_id,
            )
            if account.user_id != user_id:
                raise AccountResolutionError(
                    f"Account '{account_id}' is not available for this user."
                )
            return account

        account = await self.account_repo.get_by_user_and_app(user_id, connector_id)
        if not account:
            raise AccountResolutionError(
                f"No account connected for '{connector_id}'. Connect your account first."
            )
        return account

    async def _get_owned_account_for_auth_config(
        self,
        *,
        user_id: UUID,
        connector_id: str,
        auth_config_id: UUID,
        account_id: UUID | None,
    ) -> AccountEntity:
        if account_id is not None:
            account = await self._get_account_for_connector(
                account_id=account_id,
                connector_id=connector_id,
            )
            if account.user_id != user_id or account.auth_config_id != auth_config_id:
                raise AccountResolutionError(
                    f"Account '{account_id}' is not available for this auth config."
                )
            return account

        account = await self.account_repo.get_by_user_and_auth_config(
            user_id, auth_config_id
        )
        if not account:
            raise AccountResolutionError(
                f"No account connected for auth config '{auth_config_id}'. Connect your account first."
            )
        return account

    async def _get_account_for_connector(
        self,
        *,
        account_id: UUID,
        connector_id: str,
    ) -> AccountEntity:
        account = await self.account_repo.get(account_id)
        if not account:
            raise AccountResolutionError(f"Account '{account_id}' is not available.")
        if account.connector_id != connector_id:
            raise AccountResolutionError(
                f"Account '{account_id}' is not connected to connector '{connector_id}'."
            )
        return account

    async def _resolve_workload_account(
        self,
        *,
        connector_id: str,
        user_id: UUID,
        auth_actor: Context | None = None,
        account_id: UUID | None = None,
    ) -> AccountEntity:
        auth_ctx = auth_actor or get_current_context()
        if auth_ctx is None or auth_ctx.delegated_by_user_id is None:
            raise ConnectorAccessDeniedError("Missing delegated workload context")

        if self._is_default_pod_agent_delegation(auth_ctx):
            return await self._get_owned_account(
                user_id=user_id,
                connector_id=connector_id,
                account_id=account_id,
            )

        await self._require_delegated_access(
            auth_ctx,
            Permissions.CONNECTOR_USE,
            ResourceRef.connector(
                pod_id=auth_ctx.pod_id,
                pod_connector_id=connector_resource_id(connector_id),
            ),
        )

        if account_id is not None:
            requested_account = await self._get_account_for_connector(
                account_id=account_id,
                connector_id=connector_id,
            )
            if requested_account.user_id == user_id:
                return requested_account
            await self._require_delegated_access(
                auth_ctx,
                Permissions.CONNECTOR_ACCOUNT_USE,
                ResourceRef.connector_account(
                    pod_id=auth_ctx.pod_id,
                    pod_account_id=requested_account.id,
                ),
            )
            return requested_account

        return await self._get_owned_account(
            user_id=user_id,
            connector_id=connector_id,
            account_id=None,
        )

    @staticmethod
    def _is_default_pod_agent_delegation(ctx: Context) -> bool:
        actor_type, _, actor_id = ctx.actor_id.partition(":")
        return (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
            and actor_type == WorkloadPrincipalType.AGENT.value
            and actor_id == str(DEFAULT_POD_AGENT_ID)
            and ctx.delegation_actor_name in {None, DEFAULT_POD_AGENT_NAME}
        )

    async def _require_delegated_access(
        self,
        auth_actor: Context,
        action: str,
        resource: ResourceRef,
    ) -> None:
        try:
            await auth_actor.require(action, resource)
        except Exception as exc:
            raise ConnectorAccessDeniedError(
                "Delegated workload is not authorized",
                details={
                    "reason_code": getattr(exc, "code", "ACCESS_DENIED"),
                    "action": str(action),
                },
            ) from exc

    async def resolve_account(
        self,
        *,
        user_id: UUID,
        connector_id: str,
        auth_actor: Context | None = None,
        account_id: UUID | None = None,
    ) -> AccountEntity:
        auth_ctx = auth_actor or get_current_context()
        if auth_ctx is not None and auth_ctx.delegated_by_user_id is not None:
            return await self._resolve_workload_account(
                connector_id=connector_id,
                user_id=user_id,
                auth_actor=auth_ctx,
                account_id=account_id,
            )

        return await self._get_owned_account(
            user_id=user_id,
            connector_id=connector_id,
            account_id=account_id,
        )

    async def resolve_account_for_auth_config(
        self,
        *,
        user_id: UUID,
        connector_id: str,
        auth_config_id: UUID,
        auth_actor: Context | None = None,
        account_id: UUID | None = None,
    ) -> AccountEntity:
        auth_ctx = auth_actor or get_current_context()
        if auth_ctx is not None and auth_ctx.delegated_by_user_id is not None:
            account = await self._resolve_workload_account(
                connector_id=connector_id,
                user_id=user_id,
                auth_actor=auth_ctx,
                account_id=account_id,
            )
            if account.auth_config_id != auth_config_id:
                raise AccountResolutionError(
                    f"Account '{account.id}' is not available for auth config '{auth_config_id}'."
                )
            return account

        return await self._get_owned_account_for_auth_config(
            user_id=user_id,
            connector_id=connector_id,
            auth_config_id=auth_config_id,
            account_id=account_id,
        )
