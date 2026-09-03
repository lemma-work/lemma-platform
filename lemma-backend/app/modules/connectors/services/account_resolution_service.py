"""Resolves which connected account a connector call runs against.

Resolution rule:
- **Account owner**: a user (or a workload delegating for them) using their own
  account needs no grant beyond ``connector.use`` — the owner-match
  early-return below handles it.
- **Default pod agent**: mirrors the invoking user, so it resolves to the
  user's own account for the connector.
- **Named workload with a pinned (shared) account id**: the workload needs
  ``connector.use`` on the connector plus ``connector_account.use`` on the
  pinned account — and, because a workload's authority is its grants
  intersected with the invoking person's (PS-ACCESS-020,
  ``core/authorization/workload_authority.py``), the person driving it must be
  able to use that account too. A shared-sender setup — one team Gmail pinned
  on a function — therefore works for a pod member once they hold the account,
  and refuses with ``DELEGATION_EXCEEDS_INVOKER`` for someone who does not.
  Reaching a colleague's credential is exactly what the workload must not
  launder. A run with no invoking person (see "headless runs" there) is
  authorized on the workload's grants alone.

Connector-account *visibility* is derived (RESTRICTED iff any grant row
exists), and non-owners need a user-level grant. Pinning an account on a
workload creates a grant row and so makes the account RESTRICTED: the members
who are meant to send through it need their own ``connector_account.use``
grant, not only the workload's.
"""

from uuid import UUID

from app.core.authorization.context import ActorType, Context, ResourceRef
from app.core.authorization.current import get_current_context
from app.core.authorization.permissions import Permissions
from app.core.authorization.grants import connector_resource_id
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.errors import (
    AccountResolutionError,
    ConnectorAccessDeniedError,
)
from app.modules.connectors.domain.ports import (
    AccountRepositoryPort,
    OrganizationAccessPort,
)


class AccountResolutionService:
    def __init__(
        self,
        *,
        account_repository: AccountRepositoryPort,
        authz_read_port: object | None = None,
        authorization_service: object | None = None,
        organization_access: OrganizationAccessPort | None = None,
    ):
        self.account_repo = account_repository
        self.authz_read_port = authz_read_port
        self.authorization_service = authorization_service
        self.organization_access = organization_access

    async def _assert_owner_is_still_a_member(
        self, account: AccountEntity, organization_id: UUID | None
    ) -> None:
        """Refuse an account whose owner has left the organization.

        Removing a member deletes the `organization_members` row and nothing
        else -- the account, and the provider credential inside it, stay
        exactly where they were. An agent or schedule pinned to that account
        with `connector_account.use` therefore went on acting as the departed
        person at the provider, reading their mail and writing under their
        name, indefinitely. Nobody could clean it up either: they can no longer
        reach their own account, and there is no org-wide account listing for
        an admin to find it in.

        Checked at resolution rather than at removal because this is the moment
        that matters and the only one that cannot be skipped -- a credential
        left behind by some other path is refused here too.
        """
        if self.organization_access is None or organization_id is None:
            return
        still_a_member = await self.organization_access.user_has_organization_role(
            account.user_id, organization_id
        )
        if not still_a_member:
            raise AccountResolutionError(
                "The person who connected this account is no longer a member of "
                "this organization."
            )

    @staticmethod
    def _context_org_id(auth_ctx: Context | None) -> UUID | None:
        return getattr(auth_ctx, "organization_id", None)

    async def _get_owned_account(
        self,
        *,
        user_id: UUID,
        connector_id: str,
        account_id: UUID | None,
        organization_id: UUID | None = None,
    ) -> AccountEntity:
        if account_id is not None:
            account = await self._get_account_for_connector(
                account_id=account_id,
                connector_id=connector_id,
            )
            if account.user_id != user_id or (
                organization_id is not None
                and account.organization_id != organization_id
            ):
                raise AccountResolutionError(
                    f"Account '{account_id}' is not available for this user."
                )
            return account

        # Scope to the caller's organization when known: a user may connect the
        # same connector under several orgs, and one org's run must not resolve
        # another org's account.
        account = (
            await self.account_repo.get_by_user_org_and_app(
                user_id, organization_id, connector_id
            )
            if organization_id is not None
            else await self.account_repo.get_by_user_and_app(user_id, connector_id)
        )
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
        organization_id: UUID | None = None,
    ) -> AccountEntity:
        if account_id is not None:
            account = await self._get_account_for_connector(
                account_id=account_id,
                connector_id=connector_id,
            )
            if (
                account.user_id != user_id
                or account.auth_config_id != auth_config_id
                or (
                    organization_id is not None
                    and account.organization_id != organization_id
                )
            ):
                raise AccountResolutionError(
                    f"Account '{account_id}' is not available for this auth config."
                )
            return account

        account = (
            await self.account_repo.get_by_user_org_and_auth_config(
                user_id, organization_id, auth_config_id
            )
            if organization_id is not None
            else await self.account_repo.get_by_user_and_auth_config(
                user_id, auth_config_id
            )
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
        organization_id = self._context_org_id(auth_ctx)

        if self._is_default_pod_agent_delegation(auth_ctx):
            return await self._get_owned_account(
                user_id=user_id,
                connector_id=connector_id,
                account_id=account_id,
                organization_id=organization_id,
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
            # A workload runs inside one pod (hence one org); an account from a
            # different org is never in scope, even the invoker's own.
            if (
                organization_id is not None
                and requested_account.organization_id != organization_id
            ):
                raise AccountResolutionError(
                    f"Account '{account_id}' is not available in this organization."
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
            await self._assert_owner_is_still_a_member(
                requested_account, organization_id
            )
            return requested_account

        return await self._get_owned_account(
            user_id=user_id,
            connector_id=connector_id,
            account_id=None,
            organization_id=organization_id,
        )

    @staticmethod
    def _is_default_pod_agent_delegation(ctx: Context) -> bool:
        """Whether this context is the pod's assistant acting as its user.

        ``is_user_equivalent`` is set in exactly one place -- the branch of
        ``build_delegated_workload_context`` that the assistant takes -- so
        reading it is the same answer the context was built from. Re-deriving
        it here from ``actor_id`` meant parsing a string into a type and an id
        and re-comparing both, which is how this came to disagree with the
        builder in two independent ways at once.
        """
        return (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
            and ctx.is_user_equivalent
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
            # The two halves of the intersection fail for opposite reasons and
            # are fixed in opposite places -- grant the workload, or raise the
            # access of the person running it -- so say which one gave way
            # rather than sending everyone to look at the workload's grants.
            reason_code = str(getattr(exc, "code", "ACCESS_DENIED"))
            raise ConnectorAccessDeniedError(
                "The person running this workload is not allowed to use this "
                "connector account themselves"
                if reason_code == "DELEGATION_EXCEEDS_INVOKER"
                else "Delegated workload is not authorized",
                details={"reason_code": reason_code, "action": str(action)},
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
            organization_id=self._context_org_id(auth_ctx),
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
            organization_id=self._context_org_id(auth_ctx),
        )
