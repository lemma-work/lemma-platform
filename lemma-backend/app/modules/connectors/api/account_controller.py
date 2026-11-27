from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.api.schemas import ErrorResponse
from app.core.api.pagination import parse_uuid_page_token
from app.core.app_pin import refuse_other_pod
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.connectors.api.dependencies import ConnectorServiceDep
from app.modules.connectors.api.schemas import (
    AccountCreateSchema,
    AccountInstallationsSchema,
    InstallationBindSchema,
    InstallationChoiceSchema,
    AccountCredentialsUpdateSchema,
    AccountListResponseSchema,
    AccountResponseSchema,
    MessageResponseSchema,
)
from app.modules.connectors.contracts.github import install_state_for
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationNotHeldError,
)
from app.modules.connectors.services.auth.github_bindings import (
    claim_for_account,
    held_proven_by,
    release_for_organization,
)
from app.modules.connectors.services.auth.github_installation import (
    administers,
    verify_installation,
)
from app.modules.connectors.services.auth.github_reconciler import (
    GithubInstallationReconciler,
)
from app.modules.connectors.services.credential_freshness import fresh_credentials
from app.modules.connectors.services.account_credential_rotation import (
    rotate_account_credentials,
)

router = APIRouter(
    prefix="/organizations/{organization_id}/connectors/accounts",
    tags=["Connectors"],
)
github_router = APIRouter(
    prefix="/organizations/{organization_id}/connectors/github",
    tags=["Connectors"],
)


async def _account_response(connector_service, account) -> AccountResponseSchema:
    response = AccountResponseSchema.model_validate(account)
    response.kind = await connector_service.get_account_kind(account)
    # Derived, not asked: listing accounts must not spend a GitHub call each,
    # and must not block a page on a provider being slow. `installations` below
    # is where the network lives.
    response.install_state = install_state_for(
        account.connector_id, account.external_ref
    ).value
    return response


@router.get(
    "",
    response_model=AccountListResponseSchema,
    operation_id="connector.account.list",
    summary="List Accounts",
    description="Get all connected accounts for the current user. Optionally filter by connector_id or connector_name",
)
async def list_accounts(
    user: CurrentUser,
    organization_id: UUID,
    connector_service: ConnectorServiceDep,
    connector_id: str | None = Query(default=None),
    limit: int = Query(default=100),
    page_token: str | None = Query(default=None),
) -> AccountListResponseSchema:
    cursor = parse_uuid_page_token(page_token)

    accounts, next_cursor = await connector_service.list_accounts(
        user.id,
        organization_id,
        connector_id=connector_id,
        limit=limit,
        cursor=cursor,
    )

    return AccountListResponseSchema(
        items=[
            await _account_response(connector_service, account) for account in accounts
        ],
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.post(
    "",
    response_model=AccountResponseSchema,
    operation_id="connector.account.create",
    summary="Create Account",
    description=(
        "Directly connect a credential-managed native account for an org auth config."
    ),
)
async def create_account(
    request: Request,
    user: CurrentUser,
    organization_id: UUID,
    payload: AccountCreateSchema,
    connector_service: ConnectorServiceDep,
) -> AccountResponseSchema:
    # An app's page connects into its own pod only (see the connect request).
    refuse_other_pod(request, payload.pod_id, always=True)
    from app.modules.connectors.services.connect_into_pod import (
        add_connected_to_pod,
        require_member_of,
    )

    if payload.pod_id is not None:
        await require_member_of(
            connector_service.uow,
            pod_id=payload.pod_id,
            user_id=user.id,
            organization_id=organization_id,
        )
    account = await connector_service.create_account(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_id=payload.auth_config_id,
        auth_config_name=payload.auth_config_name,
        credentials=payload.credentials,
        provider_account_id=payload.provider_account_id,
        email=payload.email,
        preferences=payload.preferences,
        allowed_scopes=payload.allowed_scopes,
    )
    if payload.pod_id is not None:
        # The account is committed already, and creating it may have handed
        # the request's session through discovery: this is its own short write.
        async with get_uow_factory()() as uow:
            await add_connected_to_pod(uow, payload.pod_id, account)
    return await _account_response(connector_service, account)


# `PATCH /{account_id}`, not `/{account_id}/credentials`. A test asserts that
# GET on the latter returns 404 -- deliberately, so raw credentials can never
# be read back through the public API, and explicitly as a tripwire against "a
# future route registration". Registering any method there makes GET a 405
# instead, which trips it. The guard is worth more than the tidier path, and
# PATCH on the resource is the more conventional shape anyway.
@router.patch(
    "/{account_id}",
    response_model=AccountResponseSchema,
    operation_id="connector.account.update",
    summary="Update Account",
    description=(
        "Replace a credential-managed account's credential, keeping the account "
        "and its id. Rotating by deleting and reconnecting issues a new id and "
        "strands every schedule, surface and grant that referenced the old one."
    ),
)
async def rotate_account_credentials_endpoint(
    user: CurrentUser,
    organization_id: UUID,
    account_id: UUID,
    payload: AccountCredentialsUpdateSchema,
    connector_service: ConnectorServiceDep,
) -> AccountResponseSchema:
    account = await rotate_account_credentials(
        connector_service,
        account_id=account_id,
        user_id=user.id,
        organization_id=organization_id,
        credentials=payload.credentials,
    )
    return await _account_response(connector_service, account)


@router.get(
    "/{account_id}",
    response_model=AccountResponseSchema,
    operation_id="connector.account.get",
    summary="Get Account",
    description="Get a specific account by ID",
)
async def get_account(
    user: CurrentUser,
    organization_id: UUID,
    account_id: UUID,
    connector_service: ConnectorServiceDep,
) -> AccountResponseSchema:
    account = await connector_service.get_account(account_id, user.id, organization_id)
    return await _account_response(connector_service, account)


@router.delete(
    "/{account_id}",
    response_model=MessageResponseSchema,
    operation_id="connector.account.delete",
    summary="Delete Account",
    description="Delete a connected account and revoke the connection",
    status_code=200,
)
async def delete_account(
    user: CurrentUser,
    organization_id: UUID,
    account_id: UUID,
    connector_service: ConnectorServiceDep,
) -> MessageResponseSchema:
    await connector_service.delete_account(account_id, user.id, organization_id)
    return MessageResponseSchema(message="Account deleted successfully", success=True)


def _installations_response(outcome) -> AccountInstallationsSchema:
    return AccountInstallationsSchema(
        install_state=outcome.state.value,
        installation_id=outcome.installation_id,
        choices=[
            InstallationChoiceSchema(
                installation_id=choice.installation_id,
                account_login=choice.account_login,
                account_type=choice.account_type,
                repository_selection=choice.repository_selection,
                manage_url=choice.manage_url,
            )
            for choice in outcome.choices
        ],
    )


@router.get(
    "/{account_id}/github/installations",
    response_model=AccountInstallationsSchema,
    operation_id="connector.account.installations",
    summary="Account Installations",
    description=(
        "Which GitHub App installations this account can reach, resolving and "
        "recording one when it is unambiguous."
    ),
)
async def account_installations(
    user: CurrentUser,
    organization_id: UUID,
    account_id: UUID,
    connector_service: ConnectorServiceDep,
    refresh: bool = Query(
        default=False,
        description=(
            "Ask the provider again rather than trusting what is recorded. "
            "Editing an installation's repositories sends no callback and no "
            "reliable event, so this is how a change made on GitHub is seen."
        ),
    ),
) -> AccountInstallationsSchema:
    """Ask GitHub what this account reaches, and record an unambiguous answer.

    Separate from listing accounts on purpose. This is the call that can be slow
    or fail, and a connectors page must not block on a provider to render rows
    it already has.
    """
    account = await connector_service.get_account(account_id, user.id, organization_id)
    reconciler = GithubInstallationReconciler(connector_service)
    return _installations_response(await reconciler.outcome(account, force=refresh))


@router.post(
    "/{account_id}/github/installations",
    response_model=AccountResponseSchema,
    operation_id="connector.account.bind_installation",
    summary="Bind Account Installation",
    description="Bind an account to one of the installations it can reach.",
)
async def bind_account_installation(
    user: CurrentUser,
    organization_id: UUID,
    account_id: UUID,
    data: InstallationBindSchema,
    connector_service: ConnectorServiceDep,
) -> AccountResponseSchema:
    """Settle which installation an account speaks for.

    Somebody in two organizations that both installed the App has two, and
    binding to whichever came back first would route the other organization's
    events at them. So the choice is theirs -- but it is still proved before it
    is stored: `external_ref` is the inbound routing key, and a request body is
    exactly as untrustworthy as the callback parameter GitHub warns about.
    """
    account = await connector_service.get_account(account_id, user.id, organization_id)
    credentials = await fresh_credentials(
        account, user.id, connector_service=connector_service
    )
    token = (credentials or {}).get("access_token")
    reveal = getattr(token, "get_secret_value", None)
    if callable(reveal):
        token = reveal()
    session = connector_service.uow.session
    # Not this organization's on a proven claim: binding it here -- or
    # stamping a binding migration 0063 derived -- needs proof this person
    # controls it on GitHub's side, not only that they can see it (audit H4).
    needs_proof = not await held_proven_by(
        session, data.installation_id, organization_id
    )
    proved = administered = False
    if token:
        # The write below wants the session back, so only the round trips to
        # GitHub happen without a pooled connection held.
        async with connection_released(session):
            proved = await verify_installation(str(token), data.installation_id)
            if proved and needs_proof:
                administered = await administers(str(token), data.installation_id)
    if not proved:
        raise ConnectorValidationError(
            "That installation is not one this account can reach."
        )
    # Reaching it is not owning it (audit H4): one this organization does not
    # hold needs its GitHub owner, whoever else holds it.
    await claim_for_account(
        session, account, data.installation_id, administered=administered
    )
    # The routing key alone: `account` was read before `fresh_credentials`,
    # which may have refreshed its token since, and a whole-row write would
    # put the spent one back (`AccountRepository.set_external_ref`).
    await connector_service.account_repository.set_external_ref(
        account.id, data.installation_id
    )
    await connector_service.uow.commit()
    account.external_ref = data.installation_id
    await GithubInstallationReconciler(connector_service).invalidate(account.id)
    return await _account_response(connector_service, account)


@github_router.delete(
    "/installations/{installation_id}",
    response_model=MessageResponseSchema,
    operation_id="connector.github_installation.release",
    responses={
        404: {
            "model": ErrorResponse,
            "description": "This organization does not hold that installation",
        }
    },
    summary="Stop Using A GitHub Installation",
    description=(
        "This organization lets go of a GitHub App installation it holds. "
        "Other organizations holding the same installation keep theirs. Its "
        "accounts naming the installation forget it, and its schedules stop "
        "hearing the installation's events. Reading an account does not take "
        "it back; picking it on an account, or connecting through it, does. "
        "Needs `org.installation.manage`."
    ),
    status_code=200,
)
async def release_github_installation(
    user: CurrentUser,
    organization_id: UUID,
    installation_id: str,
    connector_service: ConnectorServiceDep,
) -> MessageResponseSchema:
    await connector_service._require_org_member(
        user_id=user.id,
        organization_id=organization_id,
        permission=Permissions.ORG_INSTALLATION_MANAGE,
    )
    forgot = await release_for_organization(
        connector_service.uow.session, installation_id, organization_id
    )
    if forgot is None:
        raise GithubInstallationNotHeldError()
    await connector_service.uow.commit()
    reconciler = GithubInstallationReconciler(connector_service)
    for account_id in forgot:
        await reconciler.invalidate(account_id)
    return MessageResponseSchema(
        message="This organization no longer uses that GitHub installation.",
        success=True,
    )
