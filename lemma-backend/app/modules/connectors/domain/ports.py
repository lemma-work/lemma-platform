"""Connector module ports."""

from __future__ import annotations

from typing import Any, Optional, Protocol, Sequence, Tuple
from uuid import UUID

from app.modules.connectors.domain.account import AccountEntity, OAuthCredentials
from app.modules.connectors.domain.connector import (
    ConnectorEntity,
    OAuth2Defaults,
)
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
)
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.modules.connectors.domain.connect_request import ConnectRequestEntity


class ConnectorRepositoryPort(Protocol):
    async def get(self, id: str) -> Optional[ConnectorEntity]: ...

    async def update(self, entity: ConnectorEntity) -> ConnectorEntity: ...

    async def list_active(
        self, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[ConnectorEntity], Optional[str]]: ...


class AccountRepositoryPort(Protocol):
    async def create(self, entity: AccountEntity) -> AccountEntity: ...

    async def update(self, entity: AccountEntity) -> AccountEntity: ...

    async def get(self, id: UUID) -> Optional[AccountEntity]: ...

    async def delete(self, id: UUID) -> bool: ...

    async def get_by_user_and_app(
        self, user_id: UUID, connector_id: str
    ) -> Optional[AccountEntity]: ...

    async def get_by_user_org_and_app(
        self, user_id: UUID, organization_id: UUID, connector_id: str
    ) -> Optional[AccountEntity]: ...

    async def get_by_user_and_auth_config(
        self, user_id: UUID, auth_config_id: UUID
    ) -> Optional[AccountEntity]: ...

    async def get_by_user_auth_config_and_provider_account(
        self,
        user_id: UUID,
        auth_config_id: UUID,
        provider_account_id: str,
    ) -> Optional[AccountEntity]: ...

    async def list_by_auth_config(
        self, auth_config_id: UUID
    ) -> Sequence[AccountEntity]: ...

    async def list_by_user(
        self,
        user_id: UUID,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> Tuple[Sequence[AccountEntity], UUID | None]: ...

    async def list_by_user_and_org(
        self,
        user_id: UUID,
        organization_id: UUID,
        connector_id: str | None = None,
        limit: int = 100,
        cursor: UUID | None = None,
    ) -> Tuple[Sequence[AccountEntity], UUID | None]: ...


class ConnectRequestRepositoryPort(Protocol):
    async def create(self, entity: ConnectRequestEntity) -> ConnectRequestEntity: ...

    async def update(self, entity: ConnectRequestEntity) -> ConnectRequestEntity: ...

    async def get_by_state(self, state: str) -> Optional[ConnectRequestEntity]: ...


class ConnectorTriggerRepositoryPort(Protocol):
    async def get(self, id: str) -> Optional[ConnectorTriggerEntity]: ...

    async def list_all(
        self,
        connector_id: Optional[str] = None,
        search_query: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[ConnectorTriggerEntity], Optional[str]]: ...

    async def list_by_connector_provider(
        self,
        connector_id: str,
        provider: str,
        search_query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Sequence[ConnectorTriggerEntity]: ...

    async def get_by_connector_provider_and_name(
        self, connector_id: str, provider: str, trigger_name: str
    ) -> Optional[ConnectorTriggerEntity]: ...


class ConnectorOperationRepositoryPort(Protocol):
    async def create(
        self, entity: ConnectorOperationEntity
    ) -> ConnectorOperationEntity: ...

    async def update(
        self, entity: ConnectorOperationEntity
    ) -> ConnectorOperationEntity: ...

    async def list_by_connector(
        self,
        connector_id: str,
        search_query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Sequence[ConnectorOperationEntity]: ...

    async def list_by_connector_provider(
        self,
        connector_id: str,
        provider: str,
        search_query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Sequence[ConnectorOperationEntity]: ...

    async def get_by_connector_and_name(
        self, connector_id: str, operation_name: str
    ) -> Optional[ConnectorOperationEntity]: ...

    async def get_by_connector_provider_and_name(
        self, connector_id: str, provider: str, operation_name: str
    ) -> Optional[ConnectorOperationEntity]: ...

    async def has_operations(self, connector_id: str) -> bool: ...


class AuthProviderPort(Protocol):
    async def get_authorization_url(
        self,
        connector: ConnectorEntity,
        user_id: UUID,
        state: str,
        redirect_uri: str,
    ) -> tuple[str, str]: ...

    async def exchange_code_for_credentials(
        self,
        connector: ConnectorEntity,
        redirect_uri: str,
        user_id: UUID,
        state: Optional[str] = None,
    ) -> OAuthCredentials: ...

    async def refresh_credentials(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> OAuthCredentials: ...

    async def revoke_connection(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> None: ...


class AuthProviderRegistryPort(Protocol):
    def get(self, provider_name: str) -> Optional[AuthProviderPort]: ...


class OAuthRedirectUriBuilderPort(Protocol):
    def build(self) -> str: ...


class SecretEncryptionPort(Protocol):
    def encrypt_json(self, value: dict[str, Any] | None) -> dict[str, Any] | None: ...

    def decrypt_json(self, value: dict[str, Any] | None) -> dict[str, Any] | None: ...


class SystemOAuthConfigPort(Protocol):
    def has_default_oauth_config(self, connector: ConnectorEntity) -> bool: ...

    def get_default_oauth_config(
        self,
        connector: ConnectorEntity,
    ) -> Any | None: ...

    def resolve_oauth2_defaults(
        self,
        connector: ConnectorEntity,
    ) -> Optional[OAuth2Defaults]: ...


class OrganizationAccessPort(Protocol):
    async def organization_exists(self, organization_id: UUID) -> bool: ...

    async def user_has_organization_role(
        self,
        user_id: UUID,
        organization_id: UUID,
        allowed_roles: Sequence[str] | None = None,
    ) -> bool: ...


class OperationDetailsPort(Protocol):
    description: str | None
    input_schema_content: str | None
    output_schema_content: str | None


class AppOperationGatewayPort(Protocol):
    async def list_operations(self, connector_id: str) -> Sequence[str]: ...

    async def get_operation_details(
        self, connector_id: str, operation_name: str
    ) -> OperationDetailsPort: ...

    async def execute_operation(
        self,
        connector_id: str,
        operation_name: str,
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
        auth_token: str | None = None,
        api_url: str | None = None,
        provider: str | None = None,
    ) -> Any: ...


class SchemaCompilerPort(Protocol):
    def to_json_schema(self, code: str) -> dict[str, Any]: ...
