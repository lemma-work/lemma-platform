from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Any

from app.core.concurrency.offload import run_blocking
from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.connector import ConnectorEntity
from app.modules.connectors.domain.errors import (
    ConnectorValidationError,
    OperationNotFoundError,
)


@dataclass(frozen=True, slots=True)
class LemmaConnectorBinding:
    package_name: str
    info_client_class: str
    client_class: str
    title: str
    description: str


class AsyncLemmaInfoClientAdapter:
    """Async face over the generated client, whose methods are anything but.

    `list_operations` builds every generated tool, importing that connector's
    API and pydantic model modules as it goes -- 1.4 seconds for jira, which is
    over the loop-stall threshold on its own. `async def` around a synchronous
    call does not move the work off the loop; `run_blocking` does.
    """

    def __init__(self, client: Any):
        self._client = client

    async def list_operations(self) -> Any:
        return await run_blocking(self._client.list_operations, limiter="cpu_bound")

    async def get_operation(self, operation_name: str) -> Any:
        return await run_blocking(
            self._client.get_operation, operation_name, limiter="cpu_bound"
        )


class AsyncLemmaExecutionClientAdapter:
    def __init__(self, client: Any):
        self._client = client

    async def list_operations(self) -> Any:
        return await run_blocking(self._client.list_operations, limiter="cpu_bound")

    async def get_operation(self, operation_name: str) -> Any:
        try:
            return await run_blocking(
                self._client.get_operation, operation_name, limiter="cpu_bound"
            )
        except Exception as exc:
            if type(exc).__name__ == "OperationNotFoundError":
                raise OperationNotFoundError(operation_name) from exc
            raise

    async def execute_operation(
        self, operation_name: str, payload: dict[str, Any]
    ) -> Any:
        return await self._client.execute_operation(operation_name, payload)


_BINDINGS: dict[str, LemmaConnectorBinding] = {
    "gmail": LemmaConnectorBinding(
        package_name="gmail",
        info_client_class="GmailInfoClient",
        client_class="GmailClient",
        title="Gmail",
        description="Native Gmail connector for mail, labels, drafts, messages, and threads.",
    ),
    "google_calendar": LemmaConnectorBinding(
        package_name="google_calendar",
        info_client_class="GoogleCalendarInfoClient",
        client_class="GoogleCalendarClient",
        title="Google Calendar",
        description="Native Google Calendar connector for calendars, events, ACLs, and availability.",
    ),
    "google_drive": LemmaConnectorBinding(
        package_name="google_drive",
        info_client_class="GoogleDriveInfoClient",
        client_class="GoogleDriveClient",
        title="Google Drive",
        description="Native Google Drive connector for files, permissions, revisions, and drives.",
    ),
    "google_docs": LemmaConnectorBinding(
        package_name="google_docs",
        info_client_class="GoogleDocsInfoClient",
        client_class="GoogleDocsClient",
        title="Google Docs",
        description="Native Google Docs connector for documents and document updates.",
    ),
    "google_sheets": LemmaConnectorBinding(
        package_name="google_sheets",
        info_client_class="GoogleSheetsInfoClient",
        client_class="GoogleSheetsClient",
        title="Google Sheets",
        description="Native Google Sheets connector for spreadsheets, values, and batch updates.",
    ),
    "slack": LemmaConnectorBinding(
        package_name="slack",
        info_client_class="SlackInfoClient",
        client_class="SlackClient",
        title="Slack",
        description="Native Slack connector for channels, messages, files, users, and workspace APIs.",
    ),
    "jira": LemmaConnectorBinding(
        package_name="jira",
        info_client_class="JiraInfoClient",
        client_class="JiraClient",
        title="Jira",
        description="Native Jira connector for issues, projects, workflows, users, attachments, and administration APIs.",
    ),
}

_PACKAGE_ALIASES: dict[str, str] = {
    "googlecalendar": "google_calendar",
    "googledrive": "google_drive",
    "googledocs": "google_docs",
    "googlesheets": "google_sheets",
}


def supported_lemma_connectors() -> set[str]:
    return set(_BINDINGS)


def resolve_lemma_package_name(connector: ConnectorEntity | str) -> str:
    if isinstance(connector, ConnectorEntity):
        connector_id = connector.id
    else:
        connector_id = connector
    return _PACKAGE_ALIASES.get(connector_id, connector_id)


def resolve_lemma_binding(connector: ConnectorEntity | str) -> LemmaConnectorBinding:
    package_name = resolve_lemma_package_name(connector)
    binding = _BINDINGS.get(package_name)
    if binding is None:
        raise ConnectorValidationError(
            f"Lemma connector package '{package_name}' is not available.",
            details={"package_name": package_name},
        )
    return binding


def build_lemma_credentials(third_party_credentials: dict[str, Any] | None) -> Any:
    auth_module = importlib.import_module("lemma_connectors.core.auth")
    ApiKeyCredentials = getattr(auth_module, "ApiKeyCredentials")
    OAuth2Credentials = getattr(auth_module, "OAuth2Credentials")

    if not third_party_credentials:
        return None
    if third_party_credentials.get("access_token"):
        scopes = third_party_credentials.get("scopes") or []
        if isinstance(scopes, str):
            scopes = [scope for scope in scopes.split() if scope]
        user_data = third_party_credentials.get("user_data") or {}
        base_url = third_party_credentials.get("base_url") or user_data.get("base_url")
        cloud_id = third_party_credentials.get("cloud_id") or user_data.get("cloud_id")
        return OAuth2Credentials(
            access_token=third_party_credentials["access_token"],
            refresh_token=third_party_credentials.get("refresh_token"),
            token_type=third_party_credentials.get("token_type") or "Bearer",
            expires_at=third_party_credentials.get("expires_at"),
            scopes=list(scopes),
            base_url=base_url,
            cloud_id=cloud_id,
        )
    if third_party_credentials.get("api_key"):
        return ApiKeyCredentials(api_key=third_party_credentials["api_key"])
    return None


def create_lemma_info_client(connector: ConnectorEntity | str) -> Any:
    binding = resolve_lemma_binding(connector)
    module = importlib.import_module(f"lemma_connectors.{binding.package_name}.client")
    return AsyncLemmaInfoClientAdapter(getattr(module, binding.info_client_class)())


def create_lemma_execution_client(
    connector: ConnectorEntity | str,
    third_party_credentials: dict[str, Any] | None,
) -> Any:
    binding = resolve_lemma_binding(connector)
    module = importlib.import_module(f"lemma_connectors.{binding.package_name}.client")
    client_class = getattr(module, binding.client_class)
    credentials = build_lemma_credentials(third_party_credentials)
    return AsyncLemmaExecutionClientAdapter(client_class(credentials=credentials))


def describe_lemma_connector(connector: ConnectorEntity | str) -> dict[str, str]:
    binding = resolve_lemma_binding(connector)
    return {
        "package_name": binding.package_name,
        "title": binding.title,
        "description": binding.description,
    }


def schema_json(schema: dict[str, Any]) -> str:
    return json.dumps(schema, sort_keys=True)


def build_native_oauth_credentials(credentials: OAuthCredentials) -> dict[str, Any]:
    return credentials.model_dump(exclude_none=True)
