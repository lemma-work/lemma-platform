from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.core.api.pagination import parse_uuid_page_token
from app.core.api.dependencies import CurrentUser
from app.core.authorization.dependencies import reject_delegated_workload
from app.modules.connectors.api.dependencies import ConnectorServiceDep
from app.modules.connectors.api.schemas import (
    AccountCreateSchema,
    AccountCredentialsResponseSchema,
    AccountListResponseSchema,
    AccountResponseSchema,
    CredentialTypes,
    MessageResponseSchema,
    OauthCredentialsResponseSchema,
)

router = APIRouter(
    prefix="/organizations/{organization_id}/connectors/accounts",
    tags=["Connectors"],
)


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
        items=[AccountResponseSchema.model_validate(account) for account in accounts],
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
    user: CurrentUser,
    organization_id: UUID,
    payload: AccountCreateSchema,
    connector_service: ConnectorServiceDep,
) -> AccountResponseSchema:
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
    return AccountResponseSchema.model_validate(account)


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
    return AccountResponseSchema.model_validate(account)


@router.get(
    "/{account_id}/credentials",
    response_model=AccountCredentialsResponseSchema,
    operation_id="connector.account.credentials.get",
    summary="Get Credentials",
    description="Get the credentials for a specific account",
)
async def get_credentials(
    user: CurrentUser,
    organization_id: UUID,
    account_id: UUID,
    connector_service: ConnectorServiceDep,
) -> AccountCredentialsResponseSchema:
    credentials = await connector_service.get_account_credentials(
        account_id, user.id, organization_id
    )

    return AccountCredentialsResponseSchema(
        type=CredentialTypes.OAUTH2,
        data=OauthCredentialsResponseSchema(
            access_token=credentials.access_token,
            expires_at=credentials.expires_at
            if hasattr(credentials, "expires_at")
            else None,
            connection_id=getattr(credentials, "connection_id", None),
        ),
        user_data=credentials.user_data if hasattr(credentials, "user_data") else None,
    )


@router.delete(
    "/{account_id}",
    response_model=MessageResponseSchema,
    operation_id="connector.account.delete",
    summary="Delete Account",
    description="Delete a connected account and revoke the connection",
    status_code=200,
    dependencies=[reject_delegated_workload("delete a connected account")],
)
async def delete_account(
    user: CurrentUser,
    organization_id: UUID,
    account_id: UUID,
    connector_service: ConnectorServiceDep,
) -> MessageResponseSchema:
    await connector_service.delete_account(account_id, user.id, organization_id)
    return MessageResponseSchema(message="Account deleted successfully", success=True)
