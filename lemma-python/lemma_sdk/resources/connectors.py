from __future__ import annotations

from ..openapi_client.api.connectors import (
    connector_get,
    connector_list,
    connector_operation_detail,
    connector_operation_details_batch,
    connector_operation_discover,
    connector_operation_execute,
    connector_trigger_get,
    connector_trigger_list,
)
from ..openapi_client.api.connectors import (
    connector_account_create,
    connector_account_credentials_get,
    connector_account_delete,
    connector_account_get,
    connector_account_list,
    connector_auth_config_create as auth_config_create,
    connector_auth_config_delete as auth_config_delete,
    connector_auth_config_get as auth_config_get,
    connector_auth_config_list as auth_config_list,
    connector_connect_request_create,
)
from ..openapi_client.models.account_create_schema import AccountCreateSchema
from ..openapi_client.models.account_credentials_response_schema import (
    AccountCredentialsResponseSchema,
)
from ..openapi_client.models.account_list_response_schema import AccountListResponseSchema
from ..openapi_client.models.account_response_schema import AccountResponseSchema
from ..openapi_client.models.connector_detail_response_schema import (
    ConnectorDetailResponseSchema,
)
from ..openapi_client.models.connector_list_response_schema import (
    ConnectorListResponseSchema,
)
from ..openapi_client.models.app_trigger_list_response_schema import (
    AppTriggerListResponseSchema,
)
from ..openapi_client.models.app_trigger_response_schema import AppTriggerResponseSchema
from ..openapi_client.models.auth_config_create_schema import AuthConfigCreateSchema
from ..openapi_client.models.auth_config_list_response_schema import (
    AuthConfigListResponseSchema,
)
from ..openapi_client.models.auth_config_response_schema import AuthConfigResponseSchema
from ..openapi_client.models.connect_request_initiate_schema import (
    ConnectRequestInitiateSchema,
)
from ..openapi_client.models.connect_request_response_schema import (
    ConnectRequestResponseSchema,
)
from ..openapi_client.models.operation_detail import OperationDetail
from ..openapi_client.models.operation_details_batch_request import (
    OperationDetailsBatchRequest,
)
from ..openapi_client.models.operation_details_batch_response import (
    OperationDetailsBatchResponse,
)
from ..openapi_client.models.operation_discover_response import OperationDiscoverResponse
from ..openapi_client.models.operation_execution_request import (
    OperationExecutionRequest,
)
from ..openapi_client.models.operation_execution_response import (
    OperationExecutionResponse,
)
from ..types import ConnectorPayload, JsonObject
from .base import BoundResource, compact


class ConnectorApps:
    def __init__(self, parent: "BoundConnectors") -> None:
        self._parent = parent

    def list(self, *, limit: int = 100) -> ConnectorListResponseSchema:
        return self._parent._call(connector_list, limit=limit)

    def get(self, app: str) -> ConnectorDetailResponseSchema:
        return self._parent._call(connector_get, app)

    def skill(self, app: str, *, provider: str | None = None) -> dict:
        """Get the skill guide markdown for a connector.

        Pass provider='lemma' or provider='composio' for apps that support both providers.
        Falls back to the generic doc when no provider-specific file exists.
        """
        from ..errors import LemmaAPIError
        http = self._parent._transport.generated.get_httpx_client()
        params = {"provider": provider} if provider else {}
        response = http.get(f"/connectors/{app}/skill", params=params)
        status_code = int(response.status_code)
        if status_code >= 400:
            message = f"No skill doc found for '{app}'" if status_code == 404 else "Request failed"
            raise LemmaAPIError(status_code=status_code, message=message)
        return response.json()


class ConnectorAuthConfigs:
    def __init__(self, parent: "BoundConnectors") -> None:
        self._parent = parent

    def list(self, *, limit: int = 100) -> AuthConfigListResponseSchema:
        return self._parent._call(auth_config_list, self._parent._org_uuid(), limit=limit)

    def get(self, name: str) -> AuthConfigResponseSchema:
        return self._parent._call(auth_config_get, self._parent._org_uuid(), name)

    def create(self, request: AuthConfigCreateSchema) -> AuthConfigResponseSchema:
        return self._parent._call(auth_config_create, self._parent._org_uuid(), body=request)

    def delete(self, name: str) -> None:
        self._parent._call(auth_config_delete, self._parent._org_uuid(), name)


class ConnectorAccounts:
    def __init__(self, parent: "BoundConnectors") -> None:
        self._parent = parent

    def list(
        self,
        *,
        app: str | None = None,
        limit: int = 100,
    ) -> AccountListResponseSchema:
        return self._parent._call(
            connector_account_list,
            self._parent._org_uuid(),
            connector_id=app,
            limit=limit,
        )

    def create(self, auth_config: str, request: AccountCreateSchema) -> AccountResponseSchema:
        return self._parent._call(
            connector_account_create,
            self._parent._org_uuid(),
            auth_config,
            body=request,
        )

    def get(self, account_id: str) -> AccountResponseSchema:
        return self._parent._call(connector_account_get, self._parent._org_uuid(), account_id)

    def credentials(self, account_id: str) -> AccountCredentialsResponseSchema:
        return self._parent._call(
            connector_account_credentials_get,
            self._parent._org_uuid(),
            account_id,
        )

    def delete(self, account_id: str) -> None:
        self._parent._call(connector_account_delete, self._parent._org_uuid(), account_id)


class ConnectorOperations:
    def __init__(self, parent: "BoundConnectors") -> None:
        self._parent = parent

    def search(
        self,
        auth_config: str,
        query: str | None = None,
        *,
        limit: int = 100,
    ) -> OperationDiscoverResponse:
        return self._parent._call(
            connector_operation_discover,
            self._parent._org_uuid(),
            auth_config,
            query=query,
            limit=limit,
        )

    discover = search
    list = search

    def get(self, auth_config: str, operation: str) -> OperationDetail:
        return self._parent._call(
            connector_operation_detail,
            self._parent._org_uuid(),
            auth_config,
            operation,
        )

    def batch(self, auth_config: str, operations: list[str]) -> OperationDetailsBatchResponse:
        return self._parent._call(
            connector_operation_details_batch,
            self._parent._org_uuid(),
            auth_config,
            body={"operation_names": operations},
            body_model=OperationDetailsBatchRequest,
        )

    def execute(
        self,
        auth_config: str,
        operation: str,
        payload: ConnectorPayload,
        *,
        account_id: str | None = None,
    ) -> OperationExecutionResponse:
        return self._parent._call(
            connector_operation_execute,
            self._parent._org_uuid(),
            auth_config,
            operation,
            body=compact({"payload": payload, "account_id": account_id}),
            body_model=OperationExecutionRequest,
        )


class ConnectorTriggers:
    def __init__(self, parent: "BoundConnectors") -> None:
        self._parent = parent

    def list(
        self,
        auth_config: str,
        *,
        search: str | None = None,
        limit: int = 100,
    ) -> AppTriggerListResponseSchema:
        return self._parent._call(
            connector_trigger_list,
            self._parent._org_uuid(),
            auth_config,
            search=search,
            limit=limit,
        )

    discover = list

    def get(self, auth_config: str, trigger: str) -> AppTriggerResponseSchema:
        return self._parent._call(
            connector_trigger_get,
            self._parent._org_uuid(),
            auth_config,
            trigger,
        )


class BoundConnectors(BoundResource):
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.apps = ConnectorApps(self)
        self.auth_configs = ConnectorAuthConfigs(self)
        self.accounts = ConnectorAccounts(self)
        self.operations = ConnectorOperations(self)
        self.triggers = ConnectorTriggers(self)

    def execute(
        self,
        auth_config: str,
        operation: str,
        payload: ConnectorPayload,
        *,
        account_id: str | None = None,
    ) -> OperationExecutionResponse:
        return self.operations.execute(
            auth_config,
            operation,
            payload,
            account_id=account_id,
        )

    def connect_request(
        self,
        app: str,
        *,
        auth_config_id: str | None = None,
    ) -> ConnectRequestResponseSchema:
        return self._call(
            connector_connect_request_create,
            self._org_uuid(),
            body=compact({"connector_id": app, "auth_config_id": auth_config_id}),
            body_model=ConnectRequestInitiateSchema,
        )

    def status(self) -> dict:
        """Return combined installed apps + connected accounts for the current org/user."""
        from ..errors import LemmaAPIError
        http = self._transport.generated.get_httpx_client()
        response = http.get(f"/organizations/{self._org_uuid()}/connectors/status")
        status_code = int(response.status_code)
        if status_code >= 400:
            raise LemmaAPIError(status_code=status_code, message="Failed to fetch connector status")
        return response.json()

    def create_auth_config_from_dict(self, payload: JsonObject) -> AuthConfigResponseSchema:
        return self.auth_configs.create(AuthConfigCreateSchema.from_dict(payload))
