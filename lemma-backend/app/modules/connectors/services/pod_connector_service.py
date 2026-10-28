"""Declaring a pod's connectors, and sharing accounts into it.

Two kinds of authority meet here and are kept apart on purpose:

- ``pod.connector.manage`` (POD_EDITOR and up) declares connectors, points a
  SHARED one at an account, and removes any account from the pod;
- ``connector_account.share`` is held by nobody but an account's owner
  (``OWNER_RESOURCE_ACTIONS``), and is the only way an account becomes usable
  by anyone else in the pod.

So an editor chooses *among* accounts already shared, and can never be the
reason somebody's credential is in front of somebody else (audit H3). That was
the whole of the problem before: any pod user could grant ``connector_account
.use`` on a colleague's raw account id to an agent, and the pod ran as them.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.grants import (
    delete_resource_sharing_grants,
    replace_resource_grantee_grant,
)
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    AccountPodUsage,
    GranteeRef,
    ImpactedPodConnector,
    InstallationImpactResponse,
    PodAccountResponse,
    PodConnectorCreateRequest,
    PodConnectorResponse,
    PodConnectorUpdateRequest,
    SharedAccountSummary,
)
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.auth_config import AuthConfigStatus
from app.modules.connectors.domain.errors import (
    AccountNotFoundError,
    ConnectorNotFoundError,
    InstallationNotFoundError,
    OrganizationConnectorsNotFoundError,
)
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodAccountSource,
    PodAccountVisibility,
    PodConnectorEntity,
    PodConnectorMode,
    connector_status,
)
from app.modules.connectors.domain.pod_connector_errors import (
    PodAccountNotFoundError,
    PodConnectorNotFoundError,
    PodConnectorValidationError,
)
from app.modules.connectors.domain.ports import (
    AccountRepositoryPort,
    OrganizationAccessPort,
)
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodAccountRepository,
    PodConnectorRepository,
)
from app.modules.connectors.infrastructure.repositories.pod_resource_grants import (
    delete_grants_on,
)
from app.modules.connectors.services.pod_connector_notices import (
    tell_editors_connectors_lost_their_account,
)
from app.modules.connectors.services.pod_connector_resolution import acting_person
from app.modules.connectors.services.pod_connector_use import (
    can_use,
    slug_to_declare,
)


def withdraw_sandbox_borrowers_after_commit(
    uow: SqlAlchemyUnitOfWork, pod_account_ids: Sequence[UUID | None]
) -> None:
    """Imported on use: only a change to whom an account serves needs it."""
    from app.modules.connectors.contracts import sandbox_borrowers

    sandbox_borrowers.withdraw_sandbox_borrowers_after_commit(uow, pod_account_ids)


class PodConnectorService:
    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        pod_connectors: PodConnectorRepository,
        pod_accounts: PodAccountRepository,
        auth_configs: AuthConfigRepository,
        accounts: AccountRepositoryPort,
        organization_access: OrganizationAccessPort,
    ):
        self.uow = uow
        self.pod_connectors = pod_connectors
        self.pod_accounts = pod_accounts
        self.auth_configs = auth_configs
        self.accounts = accounts
        self.organization_access = organization_access

    @property
    def _session(self) -> AsyncSession:
        return self.uow.session

    # -- connectors -----------------------------------------------------------

    async def list_connectors(
        self, ctx: Context, pod_id: UUID
    ) -> list[PodConnectorResponse]:
        await ctx.require(Permissions.POD_READ, ResourceRef.pod(pod_id))
        connectors = await self.pod_connectors.list_by_pod(pod_id)
        return [await self._present_connector(c, ctx) for c in connectors]

    async def usable_connectors(
        self, ctx: Context, pod_id: UUID
    ) -> list[PodConnectorResponse]:
        """The pod's connectors this actor may run operations through."""
        usable = [
            connector
            for connector in await self.pod_connectors.list_by_pod(pod_id)
            if await ctx.can(
                Permissions.CONNECTOR_USE, ResourceRef.connector(pod_id, connector.id)
            )
        ]
        return [await self._present_connector(c, ctx) for c in usable]

    async def get_connector(
        self, ctx: Context, pod_id: UUID, slug: str
    ) -> PodConnectorResponse:
        await ctx.require(Permissions.POD_READ, ResourceRef.pod(pod_id))
        return await self._present_connector(await self._connector(pod_id, slug), ctx)

    async def create_connector(
        self, ctx: Context, pod_id: UUID, data: PodConnectorCreateRequest
    ) -> PodConnectorResponse:
        await ctx.require(Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id))
        # A `connector_id` was resolved to an installation by the controller.
        auth_config = (
            await self.auth_configs.get(data.auth_config_id)
            if data.auth_config_id is not None
            else None
        )
        if auth_config is None or auth_config.organization_id != ctx.organization_id:
            raise InstallationNotFoundError(str(data.auth_config_id))
        slug = await slug_to_declare(
            self.pod_connectors, pod_id, auth_config, requested=data.slug
        )
        connector = PodConnectorEntity(
            pod_id=pod_id,
            slug=slug,
            auth_config_id=auth_config.id,
            mode=data.mode,
            required=data.required,
            purpose=data.purpose,
            visibility=data.visibility,
            created_by_user_id=acting_person(ctx),
        )
        if data.shared_pod_account_id is not None:
            connector = await self._with_shared_account(
                ctx, connector, data.shared_pod_account_id
            )
        created = await self.pod_connectors.create(connector)
        return await self._present_connector(created, ctx)

    async def update_connector(
        self, ctx: Context, pod_id: UUID, slug: str, data: PodConnectorUpdateRequest
    ) -> PodConnectorResponse:
        await ctx.require(Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id))
        connector = await self._connector(pod_id, slug)
        previous_account = connector.shared_pod_account_id
        changes = {
            field: getattr(data, field)
            for field in ("mode", "required", "purpose", "visibility")
            if field in data.model_fields_set and getattr(data, field) is not None
        }
        connector = connector.model_copy(update=changes)
        if connector.mode is PodConnectorMode.PER_MEMBER:
            connector = connector.model_copy(update={"shared_pod_account_id": None})
        if "shared_pod_account_id" in data.model_fields_set:
            if data.shared_pod_account_id is None:
                connector = connector.model_copy(update={"shared_pod_account_id": None})
            elif data.shared_pod_account_id != connector.shared_pod_account_id:
                connector = await self._with_shared_account(
                    ctx, connector, data.shared_pod_account_id
                )
        updated = await self.pod_connectors.update(connector)
        if updated.shared_pod_account_id != previous_account:
            withdraw_sandbox_borrowers_after_commit(self.uow, [previous_account])
        return await self._present_connector(updated, ctx)

    async def delete_connector(self, ctx: Context, pod_id: UUID, slug: str) -> None:
        await ctx.require(Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id))
        connector = await self._connector(pod_id, slug)
        await delete_grants_on(
            self._session,
            resource_type=ResourceType.CONNECTOR,
            resource_ids=[connector.id],
        )
        await self.pod_connectors.delete(connector.id)
        withdraw_sandbox_borrowers_after_commit(
            self.uow, [connector.shared_pod_account_id]
        )

    async def _with_shared_account(
        self, ctx: Context, connector: PodConnectorEntity, pod_account_id: UUID
    ) -> PodConnectorEntity:
        """Point a SHARED connector at an account its owner already shared.

        An editor picks among shared accounts; they cannot make one shared.
        Pointing a connector at a PERSONAL pod account would be exactly the
        act of sharing somebody else's credential, so it is refused here
        whatever authority the caller has. And they pick only among accounts
        shared *with them* (``connector_account.use``; the owner always
        holds it): a RESTRICTED account is no editor's to put in front of
        the whole pod, and every standing binding already on the connector
        moves with it.
        """
        if connector.mode is not PodConnectorMode.SHARED:
            raise PodConnectorValidationError(
                "Only a SHARED connector runs as one account.",
                reason="not_shared_mode",
            )
        pod_account = await self.pod_accounts.get(pod_account_id)
        if pod_account is None or pod_account.pod_id != connector.pod_id:
            raise PodAccountNotFoundError()
        if pod_account.auth_config_id != connector.auth_config_id:
            raise PodConnectorValidationError(
                "That account is connected through a different installation.",
                reason="installation_mismatch",
            )
        if not pod_account.is_shared:
            raise PodConnectorValidationError(
                "That account has not been shared with this pod. Only its owner "
                "can share it.",
                reason="account_not_shared",
            )
        await ctx.require(
            Permissions.CONNECTOR_ACCOUNT_USE,
            ResourceRef.connector_account(pod_account.pod_id, pod_account.id),
        )
        return connector.model_copy(update={"shared_pod_account_id": pod_account.id})

    async def _connector(self, pod_id: UUID, slug: str) -> PodConnectorEntity:
        connector = await self.pod_connectors.get_by_slug(pod_id, slug)
        if connector is None:
            raise PodConnectorNotFoundError(slug)
        return connector

    async def _present_connector(
        self, connector: PodConnectorEntity, ctx: Context
    ) -> PodConnectorResponse:
        """The connector as ``ctx`` sees it, ``can_use`` included: the one
        check the "connect yours" banner needs, asked of the caller alone."""
        auth_config = await self.auth_configs.get(connector.auth_config_id)
        shared = (
            await self.pod_accounts.get(connector.shared_pod_account_id)
            if connector.shared_pod_account_id
            else None
        )
        summary = None
        if shared is not None:
            account = await self.accounts.get(shared.account_id)
            summary = SharedAccountSummary(
                pod_account_id=shared.id,
                owner_user_id=shared.owner_user_id,
                display_name=account.display_name if account else None,
                email=account.email if account else None,
            )
        status = connector_status(
            connector,
            installation_active=auth_config is not None
            and auth_config.status is AuthConfigStatus.ACTIVE,
            shared_account=shared,
        )
        return PodConnectorResponse(
            **connector.model_dump(),
            installation_name=auth_config.name if auth_config else None,
            connector_id=auth_config.connector_id if auth_config else None,
            shared_account=summary,
            can_use=await can_use(ctx, connector, status, shared),
            status=status,
        )

    # -- pod accounts ---------------------------------------------------------

    async def list_accounts(
        self, ctx: Context, pod_id: UUID
    ) -> list[PodAccountResponse]:
        """Mine, whatever their visibility, and everyone's shared ones."""
        await ctx.require(Permissions.POD_READ, ResourceRef.pod(pod_id))
        me = acting_person(ctx)
        visible = [
            pod_account
            for pod_account in await self.pod_accounts.list_by_pod(pod_id)
            if pod_account.owner_user_id == me or pod_account.is_shared
        ]
        return await self._present_accounts(visible)

    async def add_account(
        self,
        ctx: Context,
        pod_id: UUID,
        *,
        account_id: UUID,
        visibility: PodAccountVisibility,
        share_with: list[GranteeRef],
        source: PodAccountSource = PodAccountSource.MANUAL,
    ) -> PodAccountResponse:
        """The owner puts one of their own accounts in the pod.

        ``source`` records how: by hand, or by an import that shared the
        importer's own account for a bundled SHARED connector."""
        await ctx.require(Permissions.POD_READ, ResourceRef.pod(pod_id))
        account = await self._own_account(ctx, account_id)
        pod_account = await self.pod_accounts.add(
            PodAccountEntity(
                pod_id=pod_id,
                account_id=account.id,
                auth_config_id=account.auth_config_id,
                owner_user_id=account.user_id,
                visibility=PodAccountVisibility.PERSONAL,
                source=source,
            )
        )
        if visibility is not PodAccountVisibility.PERSONAL:
            pod_account = await self._share(ctx, pod_account, visibility, share_with)
        return (await self._present_accounts([pod_account]))[0]

    async def update_account(
        self,
        ctx: Context,
        pod_id: UUID,
        pod_account_id: UUID,
        *,
        visibility: PodAccountVisibility | None,
        share_with: list[GranteeRef] | None,
        is_member_default: bool | None,
        allow_sandbox_use: bool | None = None,
    ) -> PodAccountResponse:
        pod_account = await self._pod_account(pod_id, pod_account_id)
        ref = ResourceRef.connector_account(pod_id, pod_account.id)
        # Every change here is the owner's: who an account serves, and which of
        # their accounts their own calls use.
        await ctx.require(Permissions.CONNECTOR_ACCOUNT_SHARE, ref)
        if visibility is not None or share_with is not None:
            pod_account = await self._share(
                ctx, pod_account, visibility or pod_account.visibility, share_with
            )
        if is_member_default:
            await self.pod_accounts.make_member_default(pod_account)
            pod_account = pod_account.model_copy(update={"is_member_default": True})
        notice = None
        if allow_sandbox_use is not None:
            pod_account, notice = await self._set_sandbox_use(
                pod_account, allow_sandbox_use
            )
        presented = (await self._present_accounts([pod_account]))[0]
        return presented.model_copy(update={"notice": notice})

    async def _set_sandbox_use(
        self, pod_account: PodAccountEntity, allowed: bool
    ) -> tuple[PodAccountEntity, str | None]:
        if allowed and not pod_account.is_shared:
            # Your own account already reaches your own sandbox; the opt-in is
            # about everyone else's, so it needs the account shared.
            raise PodConnectorValidationError(
                "Share the account with the pod before allowing it inside sandboxes.",
                reason="account_not_shared",
            )
        was_allowed = pod_account.allow_sandbox_use
        pod_account = await self.pod_accounts.set_sandbox_use(pod_account.id, allowed)
        if was_allowed and not allowed:
            from app.modules.connectors.contracts.sandbox_borrowers import (
                SANDBOX_USE_WITHDRAWN_NOTICE,
            )

            withdraw_sandbox_borrowers_after_commit(self.uow, [pod_account.id])
            return pod_account, SANDBOX_USE_WITHDRAWN_NOTICE
        return pod_account, None

    async def remove_account(
        self, ctx: Context, pod_id: UUID, pod_account_id: UUID
    ) -> None:
        """The owner withdraws it, or an editor takes it out of the pod."""
        pod_account = await self._pod_account(pod_id, pod_account_id)
        ref = ResourceRef.connector_account(pod_id, pod_account.id)
        if not await ctx.can(Permissions.CONNECTOR_ACCOUNT_SHARE, ref):
            await ctx.require(Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id))
        await self._remove([pod_account.id])

    async def _share(
        self,
        ctx: Context,
        pod_account: PodAccountEntity,
        visibility: PodAccountVisibility,
        share_with: list[GranteeRef] | None,
    ) -> PodAccountEntity:
        """Owner only. ``share_with=None`` keeps a RESTRICTED account's grantees."""
        ref = ResourceRef.connector_account(pod_account.pod_id, pod_account.id)
        await ctx.require(Permissions.CONNECTOR_ACCOUNT_SHARE, ref)
        # Who it serves is changing: sessions that borrowed it re-check.
        withdraw_sandbox_borrowers_after_commit(self.uow, [pod_account.id])
        keep_grantees = (
            visibility is PodAccountVisibility.RESTRICTED and share_with is None
        )
        if not keep_grantees:
            await delete_resource_sharing_grants(
                self._session,
                pod_id=pod_account.pod_id,
                resource_type=ResourceType.CONNECTOR_ACCOUNT,
                resource_id=pod_account.id,
            )
        if visibility is PodAccountVisibility.PERSONAL:
            # Withdrawn: SHARED connectors on it need another account, now, and
            # the sandbox opt-in does not outlive the sharing it was given for.
            await self.pod_connectors.clear_shared_account([pod_account.id])
            if pod_account.allow_sandbox_use:
                await self.pod_accounts.set_sandbox_use(pod_account.id, False)
        if visibility is PodAccountVisibility.RESTRICTED:
            for grantee in share_with or []:
                # A role or member from another pod would be stored, match
                # nobody, and stay behind as a dead grant. Imported here: only
                # sharing with some people needs the pod's roles.
                from app.modules.pod.contracts.grantees import require_pod_grantee

                await require_pod_grantee(
                    self._session,
                    pod_id=pod_account.pod_id,
                    grantee_type=grantee.grantee_type,
                    grantee_id=grantee.grantee_id,
                )
                await replace_resource_grantee_grant(
                    self._session,
                    pod_id=pod_account.pod_id,
                    resource_type=ResourceType.CONNECTOR_ACCOUNT,
                    resource_id=pod_account.id,
                    grantee_type=grantee.grantee_type,
                    grantee_id=grantee.grantee_id,
                    permission_ids=[Permissions.CONNECTOR_ACCOUNT_USE],
                    created_by_user_id=ctx.user_id,
                )
        return await self.pod_accounts.set_visibility(pod_account.id, visibility)

    async def _own_account(self, ctx: Context, account_id: UUID) -> AccountEntity:
        account = await self.accounts.get(account_id)
        if (
            account is None
            or account.user_id != acting_person(ctx)
            or account.organization_id != ctx.organization_id
        ):
            raise AccountNotFoundError(str(account_id))
        return account

    async def _pod_account(
        self, pod_id: UUID, pod_account_id: UUID
    ) -> PodAccountEntity:
        pod_account = await self.pod_accounts.get(pod_account_id)
        if pod_account is None or pod_account.pod_id != pod_id:
            raise PodAccountNotFoundError()
        return pod_account

    async def _present_accounts(
        self, pod_accounts: list[PodAccountEntity]
    ) -> list[PodAccountResponse]:
        used_by: dict[UUID, list[str]] = {}
        for connector in await self.pod_connectors.list_by_shared_accounts(
            [pod_account.id for pod_account in pod_accounts]
        ):
            if connector.shared_pod_account_id is not None:
                used_by.setdefault(connector.shared_pod_account_id, []).append(
                    connector.slug
                )
        presented = []
        for pod_account in pod_accounts:
            account = await self.accounts.get(pod_account.account_id)
            presented.append(
                PodAccountResponse(
                    **pod_account.model_dump(),
                    connector_id=account.connector_id if account else None,
                    display_name=account.display_name if account else None,
                    email=account.email if account else None,
                    used_by=sorted(used_by.get(pod_account.id, [])),
                )
            )
        return presented

    # -- the owner's and the org's view --------------------------------------

    async def where_used(
        self, *, user_id: UUID, account_id: UUID
    ) -> list[AccountPodUsage]:
        """Every pod one of my accounts is in, and what runs as it there."""
        account = await self.accounts.get(account_id)
        if account is None or account.user_id != user_id:
            raise AccountNotFoundError(str(account_id))
        pod_accounts = await self.pod_accounts.list_by_account(account_id)
        used_by: dict[UUID, list[str]] = {}
        for connector in await self.pod_connectors.list_by_shared_accounts(
            [pod_account.id for pod_account in pod_accounts]
        ):
            if connector.shared_pod_account_id is not None:
                used_by.setdefault(connector.shared_pod_account_id, []).append(
                    connector.slug
                )
        return [
            AccountPodUsage(
                pod_id=pod_account.pod_id,
                pod_account_id=pod_account.id,
                visibility=pod_account.visibility,
                source=pod_account.source,
                is_member_default=pod_account.is_member_default,
                last_used_at=pod_account.last_used_at,
                used_by=sorted(used_by.get(pod_account.id, [])),
            )
            for pod_account in pod_accounts
        ]

    async def impact(
        self, *, user_id: UUID, organization_id: UUID, auth_config_id: UUID
    ) -> InstallationImpactResponse:
        """What removing an installation stops, shown before anyone does it."""
        if not await self.organization_access.user_holds_organization_permission(
            user_id, organization_id, Permissions.ORG_INSTALLATION_MANAGE
        ):
            raise OrganizationConnectorsNotFoundError(str(organization_id))
        auth_config = await self.auth_configs.get(auth_config_id)
        if auth_config is None or auth_config.organization_id != organization_id:
            raise ConnectorNotFoundError(str(auth_config_id))
        connectors = await self.pod_connectors.list_by_auth_config(auth_config_id)
        accounts = await self.accounts.list_by_auth_config(auth_config_id)
        return InstallationImpactResponse(
            auth_config_id=auth_config_id,
            account_count=len(accounts),
            pod_account_count=await self.pod_accounts.count_by_auth_config(
                auth_config_id
            ),
            pod_connectors=[
                ImpactedPodConnector(
                    pod_id=connector.pod_id, slug=connector.slug, mode=connector.mode
                )
                for connector in connectors
            ],
        )

    # -- lifecycle ------------------------------------------------------------

    async def member_left(self, *, pod_id: UUID, user_id: UUID) -> None:
        """Their accounts leave the pod with them; SHARED connectors on one
        go NEEDS_ACCOUNT, and the pod's editors are told which."""
        owned = await self.pod_accounts.ids_owned_in_pod(
            pod_id=pod_id, owner_user_id=user_id
        )
        stopped = await self.pod_connectors.list_by_shared_accounts(owned)
        await self._remove(owned)
        await tell_editors_connectors_lost_their_account(
            self.uow,
            pod_id=pod_id,
            departed_user_id=user_id,
            slugs=[connector.slug for connector in stopped],
        )

    async def organization_member_left(
        self, *, user_id: UUID, account_ids: list[UUID]
    ) -> None:
        """They left the organization: each pod it holds drops them as
        ``member_left`` does, and then their org accounts there are deleted
        (``services/member_departure.py``)."""
        pod_ids: set[UUID] = set()
        for account_id in account_ids:
            pod_ids.update(
                pod_account.pod_id
                for pod_account in await self.pod_accounts.list_by_account(account_id)
            )
        for pod_id in sorted(pod_ids):
            await self.member_left(pod_id=pod_id, user_id=user_id)
        for account_id in account_ids:
            await self.accounts.delete(account_id)

    async def _remove(self, pod_account_ids: list[UUID]) -> None:
        withdraw_sandbox_borrowers_after_commit(self.uow, pod_account_ids)
        await self.pod_connectors.clear_shared_account(pod_account_ids)
        await delete_grants_on(
            self._session,
            resource_type=ResourceType.CONNECTOR_ACCOUNT,
            resource_ids=pod_account_ids,
        )
        await self.pod_accounts.delete(pod_account_ids)
