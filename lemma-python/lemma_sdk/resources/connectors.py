from __future__ import annotations

from ..openapi_client.api.connectors import (
    connector_account_create,
    connector_account_update,
    connector_account_delete,
    connector_account_get,
    connector_account_list,
    connector_connect_request_create,
    connector_get,
    connector_list,
    connector_operation_detail,
    connector_operation_details_batch,
    connector_operation_discover,
    connector_operation_execute,
    connector_operation_search,
    connector_trigger_get,
    connector_trigger_list,
)
from ..openapi_client.api.connectors import (
    connector_auth_config_create as auth_config_create,
)
from ..openapi_client.api.connectors import (
    connector_auth_config_delete as auth_config_delete,
)
from ..openapi_client.api.connectors import (
    connector_auth_config_get as auth_config_get,
)
from ..openapi_client.api.connectors import (
    connector_auth_config_list as auth_config_list,
)
from ..openapi_client.api.connectors import (
    connector_auth_config_refresh_operations as auth_config_refresh_operations,
)
from ..openapi_client.api.connectors import (
    connector_auth_config_update as auth_config_update,
)
from ..openapi_client.models.account_create_schema import AccountCreateSchema
from ..openapi_client.models.account_credentials_update_schema import (
    AccountCredentialsUpdateSchema,
)
from ..openapi_client.models.account_list_response_schema import (
    AccountListResponseSchema,
)
from ..openapi_client.models.account_response_schema import AccountResponseSchema
from ..openapi_client.models.app_trigger_list_response_schema import (
    AppTriggerListResponseSchema,
)
from ..openapi_client.models.app_trigger_response_schema import AppTriggerResponseSchema
from ..openapi_client.models.auth_config_create_schema import AuthConfigCreateSchema
from ..openapi_client.models.auth_config_list_response_schema import (
    AuthConfigListResponseSchema,
)
from ..openapi_client.models.auth_config_response_schema import AuthConfigResponseSchema
from ..openapi_client.models.auth_config_update_response_schema import (
    AuthConfigUpdateResponseSchema,
)
from ..openapi_client.models.auth_config_update_schema import AuthConfigUpdateSchema
from ..openapi_client.models.connect_request_initiate_schema import (
    ConnectRequestInitiateSchema,
)
from ..openapi_client.models.connect_request_response_schema import (
    ConnectRequestResponseSchema,
)
from ..openapi_client.models.connector_detail_response_schema import (
    ConnectorDetailResponseSchema,
)
from ..openapi_client.models.connector_list_response_schema import (
    ConnectorListResponseSchema,
)
from ..openapi_client.models.operation_detail import OperationDetail
from ..openapi_client.models.operation_details_batch_request import (
    OperationDetailsBatchRequest,
)
from ..openapi_client.models.operation_details_batch_response import (
    OperationDetailsBatchResponse,
)
from ..openapi_client.models.operation_discover_response import (
    OperationDiscoverResponse,
)
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

    def skill(self, app: str, *, kind: str | None = None) -> dict:
        """Get the skill guide markdown for a connector.

        Pass kind='package' or kind='composio' for apps that ship as both.
        Falls back to the generic doc when no kind-specific file exists.
        """
        transport = self._parent._transport
        http = transport.generated.get_httpx_client()
        params = {"kind": kind} if kind else {}
        response = http.get(f"/connectors/{app}/skill", params=params)
        if response.status_code >= 400:
            # Through the shared mapper, so a 404 here is a LemmaNotFoundError
            # like every other missing resource -- and keeps the server's code,
            # details and request id.
            raise transport.error_from_response(
                response.status_code, None, response.content, response.headers
            )
        return response.json()


class ConnectorAuthConfigs:
    def __init__(self, parent: "BoundConnectors") -> None:
        self._parent = parent

    def list(self, *, limit: int = 100) -> AuthConfigListResponseSchema:
        return self._parent._call(
            auth_config_list, self._parent._org_uuid(), limit=limit
        )

    def get(self, name: str) -> AuthConfigResponseSchema:
        return self._parent._call(auth_config_get, self._parent._org_uuid(), name)

    def create(self, request: AuthConfigCreateSchema) -> AuthConfigResponseSchema:
        return self._parent._call(
            auth_config_create, self._parent._org_uuid(), body=request
        )

    def update(
        self, name: str, request: AuthConfigUpdateSchema
    ) -> AuthConfigUpdateResponseSchema:
        """Update an install in place.

        Rotating a server URL or an OAuth app this way keeps the accounts
        attached to the install; deleting and recreating it cascades them away.
        Where the change invalidates credentials the affected accounts are
        marked for reconnect rather than removed, and the response says how
        many.
        """
        return self._parent._call(
            auth_config_update, self._parent._org_uuid(), name, body=request
        )

    def delete(self, name: str) -> None:
        self._parent._call(auth_config_delete, self._parent._org_uuid(), name)

    def refresh_operations(self, name: str) -> dict:
        """Re-discover the operations of an MCP or OpenAPI install.

        The recovery path for an install whose first discovery failed. Without
        it the only fix is delete-and-recreate, and accounts cascade from the
        install, so that disconnects every user who had connected.
        """
        return self._parent._call(
            auth_config_refresh_operations, self._parent._org_uuid(), name
        )


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

    def create(
        self, auth_config: str, request: AccountCreateSchema
    ) -> AccountResponseSchema:
        body = request.to_dict()
        auth_config_name = body.get("auth_config_name")
        auth_config_id = body.get("auth_config_id")
        if auth_config_name and auth_config_id:
            raise ValueError("Specify only one of auth_config_name or auth_config_id")
        if not auth_config_name and not auth_config_id:
            if not auth_config:
                raise ValueError(
                    "Either auth_config_name or auth_config_id is required"
                )
            body["auth_config_name"] = auth_config
        request = AccountCreateSchema.from_dict(body)
        return self._parent._call(
            connector_account_create,
            self._parent._org_uuid(),
            body=request,
        )

    def get(self, account_id: str) -> AccountResponseSchema:
        return self._parent._call(
            connector_account_get, self._parent._org_uuid(), account_id
        )

    def delete(self, account_id: str) -> None:
        self._parent._call(
            connector_account_delete, self._parent._org_uuid(), account_id
        )

    def rotate_credentials(
        self, account_id: str, credentials: dict
    ) -> AccountResponseSchema:
        """Replace a credential-managed account's credential, keeping its id.

        Deleting and reconnecting also rotates a credential, and issues a new
        account id doing it -- stranding every schedule, surface and grant that
        referenced the old one, and leaving nothing behind if the reconnect
        fails.
        """
        return self._parent._call(
            connector_account_update,
            self._parent._org_uuid(),
            account_id,
            body=AccountCredentialsUpdateSchema.from_dict({"credentials": credentials}),
        )


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

    def search_all(
        self, query: str | None = None, *, limit: int = 100
    ) -> OperationDiscoverResponse:
        """Search operations across EVERY install in the org, in one request.

        Each hit carries the `auth_config` to execute it against, so a caller
        that knows what it wants to do — but not which connector provides it —
        no longer has to fan out one request per install."""
        return self._parent._call(
            connector_operation_search,
            self._parent._org_uuid(),
            query=query,
            limit=limit,
        )

    def get(self, auth_config: str, operation: str) -> OperationDetail:
        return self._parent._call(
            connector_operation_detail,
            self._parent._org_uuid(),
            auth_config,
            operation,
        )

    def batch(
        self, auth_config: str, operations: list[str]
    ) -> OperationDetailsBatchResponse:
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
        http = self._transport.generated.get_httpx_client()
        response = http.get(f"/organizations/{self._org_uuid()}/connectors/status")
        if response.status_code >= 400:
            raise self._transport.error_from_response(
                response.status_code, None, response.content, response.headers
            )
        return response.json()

    def create_auth_config_from_dict(
        self, payload: JsonObject
    ) -> AuthConfigResponseSchema:
        return self.auth_configs.create(AuthConfigCreateSchema.from_dict(payload))
