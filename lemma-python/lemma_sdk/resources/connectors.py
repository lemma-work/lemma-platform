from __future__ import annotations

import warnings
from typing import Any
from uuid import UUID

from ..openapi_client.api.connectors import (
    connector_account_create,
    connector_account_update,
    connector_account_delete,
    connector_account_get,
    connector_account_list,
    connector_account_bind_installation,
    connector_account_installations,
    connector_connect_request_create,
    connector_connect_request_install,
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
from ..openapi_client.models.account_installations_schema import (
    AccountInstallationsSchema,
)
from ..openapi_client.models.connect_request_initiate_schema import (
    ConnectRequestInitiateSchema,
)
from ..openapi_client.models.install_request_initiate_schema import (
    InstallRequestInitiateSchema,
)
from ..openapi_client.models.install_request_response_schema import (
    InstallRequestResponseSchema,
)
from ..openapi_client.models.installation_bind_schema import InstallationBindSchema
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
from ..openapi_client.models.operation_execution_response import (
    OperationExecutionResponse,
)
from ..openapi_client.api.connectors import (
    pod_connector_account_add,
    pod_connector_account_list,
    pod_connector_account_remove,
    pod_connector_account_update,
    pod_connector_create,
    pod_connector_delete,
    pod_connector_get,
    pod_connector_list,
    pod_connector_operation_execute,
    pod_connector_operation_get,
    pod_connector_operation_list,
    pod_connector_update,
    pod_connector_account_share_as,
    pod_connector_ask_members,
    pod_connector_coverage,
)
from ..openapi_client.models.installation_operation_execution_request import (
    InstallationOperationExecutionRequest,
)
from ..openapi_client.models.operation_execution_request import (
    OperationExecutionRequest,
)
from ..openapi_client.models.pod_account_create_request import PodAccountCreateRequest
from ..openapi_client.models.pod_account_list_response import PodAccountListResponse
from ..openapi_client.models.pod_account_response import PodAccountResponse
from ..openapi_client.models.pod_account_update_request import PodAccountUpdateRequest
from ..openapi_client.models.pod_connector_create_request import (
    PodConnectorCreateRequest,
)
from ..openapi_client.models.pod_connector_list_response import (
    PodConnectorListResponse,
)
from ..openapi_client.models.ask_members_request import AskMembersRequest
from ..openapi_client.models.ask_members_response import AskMembersResponse
from ..openapi_client.models.pod_account_share_as_request import (
    PodAccountShareAsRequest,
)
from ..openapi_client.models.pod_connector_coverage import PodConnectorCoverage
from ..openapi_client.models.pod_connector_response import PodConnectorResponse
from ..openapi_client.models.pod_connector_update_request import (
    PodConnectorUpdateRequest,
)
from ..errors import LemmaDeprecationWarning, LemmaNotFoundError
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
        pod_id: str | None = None,
    ) -> OperationExecutionResponse:
        """Deprecated: run ``operation`` on an installation, by its name.

        Use ``connectors.execute(slug, operation, payload)``, which runs through
        the pod connector. This still works -- the server serves it through the
        connectors the pod declares for ``auth_config`` -- and warns with
        :class:`~lemma_sdk.LemmaDeprecationWarning` saying so.
        """
        pod = pod_id if pod_id is not None else self._parent.pod_id
        return self._parent._call(
            connector_operation_execute,
            self._parent._org_uuid(),
            auth_config,
            operation,
            body=compact(
                {
                    "payload": payload,
                    "account_id": account_id,
                    "pod_id": str(pod) if pod is not None else None,
                }
            ),
            body_model=InstallationOperationExecutionRequest,
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

    # -- the pod's connectors ------------------------------------------------
    #
    # Operations run through a pod connector, named by its pod-local slug
    # (`gmail`, `support-inbox`). The connector decides the account: the
    # caller's own for a PER_MEMBER connector, the one its owner shared for a
    # SHARED one -- so nothing here takes an account.

    def execute(
        self,
        connector: str,
        operation: str,
        payload: ConnectorPayload,
        *,
        account_id: str | None = None,
        pod_id: str | None = None,
    ) -> OperationExecutionResponse:
        """Run ``operation`` through the pod connector ``connector`` (its slug).

        A file argument takes a reference -- ``lemma_sdk.pod_file("/me/a.pdf")``
        -- which resolves in this pod.

        A connector that cannot run says why, as a typed error whose
        ``code`` names the remedy and whose ``connect_url`` (when there is one)
        is where to fix it: ``CONNECTION_REQUIRED`` / ``REAUTH_REQUIRED`` (connect
        or reconnect your account), ``CONNECTOR_NEEDS_ACCOUNT`` (nobody has
        shared an account for a shared connector), ``CONNECTOR_UNAVAILABLE``
        (the installation is disabled), ``CONNECTION_REQUIRES_PERSON`` (a
        per-member connector, called with no member behind the call).

        Code written before pod connectors passed an installation's name and an
        ``account_id``. That still runs, through the deprecated org-level route,
        and warns: when ``account_id`` or ``pod_id`` is given, when there is no
        pod, or when ``connector`` is not one of the pod's connectors.
        """
        if account_id is not None or pod_id is not None or self.pod_id is None:
            warnings.warn(
                "connectors.execute(..., account_id=, pod_id=) is deprecated: "
                "pass the pod connector's slug and drop account_id; the "
                "connector decides the account.",
                LemmaDeprecationWarning,
                stacklevel=2,
            )
            return self.operations.execute(
                connector, operation, payload, account_id=account_id, pod_id=pod_id
            )
        try:
            return self._call(
                pod_connector_operation_execute,
                self._pod_uuid(),
                connector,
                operation,
                body={"payload": payload},
                body_model=OperationExecutionRequest,
            )
        except LemmaNotFoundError as missing:
            if missing.code != "POD_CONNECTOR_NOT_FOUND":
                raise
            # Not a slug: most likely an installation name, as written before
            # pod connectors. The server answers that one with its own warning.
            return self.operations.execute(connector, operation, payload)

    def pod_connectors(self) -> PodConnectorListResponse:
        """The connectors this pod declares, with their mode and status."""
        return self._call(pod_connector_list, self._pod_uuid())

    def pod_connector(self, connector: str) -> PodConnectorResponse:
        """One pod connector, by slug."""
        return self._call(pod_connector_get, self._pod_uuid(), connector)

    def connector_coverage(self, connector: str) -> PodConnectorCoverage:
        """Who of the members allowed to use a required PER_MEMBER
        ``connector`` has connected an account. Needs ``pod.connector.manage``."""
        return self._call(pod_connector_coverage, self._pod_uuid(), connector)

    def ask_members_to_connect(
        self, connector: str, *, user_ids: list[str] | None = None
    ) -> AskMembersResponse:
        """Ask the members a PER_MEMBER ``connector`` is waiting on to connect,
        at most once a day each; ``user_ids`` asks only those of them."""
        return self._call(
            pod_connector_ask_members,
            self._pod_uuid(),
            connector,
            body=compact({"user_ids": user_ids}),
            body_model=AskMembersRequest,
        )

    def share_pod_account_as(
        self, pod_account_id: str, slug: str, *, purpose: str | None = None
    ) -> PodConnectorResponse:
        """Share one of your pod accounts as the SHARED connector ``slug`` in
        one step: both happen, or neither."""
        return self._call(
            pod_connector_account_share_as,
            self._pod_uuid(),
            UUID(str(pod_account_id)),
            body=compact({"slug": slug, "purpose": purpose}),
            body_model=PodAccountShareAsRequest,
        )

    def pod_operations(
        self, connector: str, query: str | None = None, *, limit: int = 100
    ) -> OperationDiscoverResponse:
        """Search the operations a pod connector offers."""
        return self._call(
            pod_connector_operation_list,
            self._pod_uuid(),
            connector,
            **compact({"query": query, "limit": limit}),
        )

    def pod_operation(self, connector: str, operation: str) -> OperationDetail:
        """One operation's input and output schema, through a pod connector."""
        return self._call(
            pod_connector_operation_get, self._pod_uuid(), connector, operation
        )

    def declare_pod_connector(
        self,
        slug: str,
        auth_config_id: str,
        mode: str,
        *,
        shared_pod_account_id: str | None = None,
        required: bool = False,
        purpose: str | None = None,
    ) -> PodConnectorResponse:
        """Declare one of the org's installations in this pod, as ``slug``.

        ``mode`` is ``PER_MEMBER`` (each caller's own account) or ``SHARED``
        (the one pod account ``shared_pod_account_id`` names, which its owner
        must already have shared). Needs ``pod.connector.manage``.
        """
        return self._call(
            pod_connector_create,
            self._pod_uuid(),
            body=compact(
                {
                    "slug": slug,
                    "auth_config_id": auth_config_id,
                    "mode": mode,
                    "shared_pod_account_id": shared_pod_account_id,
                    "required": required,
                    "purpose": purpose,
                }
            ),
            body_model=PodConnectorCreateRequest,
        )

    def update_pod_connector(
        self, connector: str, changes: JsonObject
    ) -> PodConnectorResponse:
        """Change a pod connector's mode, shared account, purpose or visibility."""
        return self._call(
            pod_connector_update,
            self._pod_uuid(),
            connector,
            body=changes,
            body_model=PodConnectorUpdateRequest,
        )

    def remove_pod_connector(self, connector: str) -> None:
        """Stop the pod using ``connector``; the org installation stays."""
        self._call(pod_connector_delete, self._pod_uuid(), connector)

    def pod_accounts(self) -> PodAccountListResponse:
        """The accounts in use in this pod that the caller can see."""
        return self._call(pod_connector_account_list, self._pod_uuid())

    def add_pod_account(
        self, account_id: str, *, visibility: str = "PERSONAL"
    ) -> PodAccountResponse:
        """Bring one of your own accounts into this pod.

        ``visibility="POD"`` shares it with the whole pod, so a SHARED
        connector can run as it. Only an account's owner can do this.
        """
        return self._call(
            pod_connector_account_add,
            self._pod_uuid(),
            body={"account_id": account_id, "visibility": visibility},
            body_model=PodAccountCreateRequest,
        )

    def update_pod_account(
        self, pod_account_id: str, changes: JsonObject
    ) -> PodAccountResponse:
        """Change an account's visibility in this pod; only its owner can."""
        return self._call(
            pod_connector_account_update,
            self._pod_uuid(),
            UUID(str(pod_account_id)),
            body=changes,
            body_model=PodAccountUpdateRequest,
        )

    def remove_pod_account(self, pod_account_id: str) -> None:
        """Take an account out of this pod."""
        self._call(
            pod_connector_account_remove, self._pod_uuid(), UUID(str(pod_account_id))
        )

    def connect_request(
        self,
        app: str,
        *,
        auth_config_id: str | None = None,
        connection_fields: dict[str, Any] | None = None,
    ) -> ConnectRequestResponseSchema:
        """Start a browser sign-in for ``app``.

        ``connection_fields`` carries what the sign-in cannot, such as
        Shopify's store ``subdomain``; the connector kind's ``config_schema``
        says which fields exist.
        """
        return self._call(
            connector_connect_request_create,
            self._org_uuid(),
            body=compact(
                {
                    "connector_id": app,
                    "auth_config_id": auth_config_id,
                    "connection_fields": connection_fields,
                }
            ),
            body_model=ConnectRequestInitiateSchema,
        )

    def install_request(self, account_id: str) -> InstallRequestResponseSchema:
        """Where to send somebody who authorized but has not installed.

        A GitHub App's user token reaches only the repositories the App is
        *installed* on, so authorizing alone yields a working token that can
        read nothing. The returned URL carries a single-use state that expires
        in thirty minutes, so ask for it when it is about to be followed.
        """
        return self._call(
            connector_connect_request_install,
            self._org_uuid(),
            body=compact({"account_id": account_id}),
            body_model=InstallRequestInitiateSchema,
        )

    def installations(
        self, account_id: str, *, refresh: bool = False
    ) -> AccountInstallationsSchema:
        """Which GitHub App installations an account can reach.

        `refresh` asks GitHub again rather than trusting what is recorded.
        Editing an installation's repositories sends no callback and no
        reliable event, so this is how such a change is ever seen.
        """
        return self._call(
            connector_account_installations,
            self._org_uuid(),
            account_id,
            refresh=refresh,
        )

    def bind_installation(
        self, account_id: str, installation_id: str
    ) -> AccountResponseSchema:
        """Settle which installation an account speaks for.

        Somebody in two organizations that both installed the App has two, and
        binding to whichever came back first would route the other
        organization's events at them.
        """
        return self._call(
            connector_account_bind_installation,
            self._org_uuid(),
            account_id,
            body=compact({"installation_id": installation_id}),
            body_model=InstallationBindSchema,
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
