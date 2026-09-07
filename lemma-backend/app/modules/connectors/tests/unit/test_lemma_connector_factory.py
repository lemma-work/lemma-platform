from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.modules.connectors.infrastructure.adapters.lemma_connector_factory import (
    build_lemma_credentials,
    create_lemma_execution_client,
    create_lemma_info_client,
    resolve_lemma_package_name,
)


def test_build_lemma_credentials_preserves_oauth_base_url_and_cloud_id():
    credentials = build_lemma_credentials(
        {
            "access_token": "access-token",
            "token_type": "Bearer",
            "user_data": {
                "base_url": "https://api.atlassian.com/ex/jira/cloud-123",
                "cloud_id": "cloud-123",
            },
        }
    )

    assert credentials is not None
    assert credentials.base_url == "https://api.atlassian.com/ex/jira/cloud-123"
    assert credentials.cloud_id == "cloud-123"


def test_create_lemma_info_client_imports_only_target_client_module():
    imported_modules: list[str] = []

    class FakeInfoClient:
        def list_operations(self):
            return []

        def get_operation(self, operation_name: str):
            return {"name": operation_name}

    def fake_import_module(module_name: str):
        imported_modules.append(module_name)
        if module_name == "lemma_connectors.google_drive.client":
            return SimpleNamespace(GoogleDriveInfoClient=FakeInfoClient)
        raise AssertionError(f"Unexpected import: {module_name}")

    with patch(
        "app.modules.connectors.infrastructure.adapters.lemma_connector_factory.importlib.import_module",
        side_effect=fake_import_module,
    ):
        client = create_lemma_info_client("google_drive")

    assert imported_modules == ["lemma_connectors.google_drive.client"]
    assert client is not None


def test_create_lemma_execution_client_imports_only_target_client_and_auth_modules():
    imported_modules: list[str] = []

    class FakeOAuth2Credentials:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeApiKeyCredentials:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeClient:
        def __init__(self, credentials=None):
            self.credentials = credentials

        async def execute_operation(self, operation_name: str, payload: dict):
            return {"operation_name": operation_name, "payload": payload}

    def fake_import_module(module_name: str):
        imported_modules.append(module_name)
        if module_name == "lemma_connectors.core.auth":
            return SimpleNamespace(
                ApiKeyCredentials=FakeApiKeyCredentials,
                OAuth2Credentials=FakeOAuth2Credentials,
            )
        if module_name == "lemma_connectors.google_drive.client":
            return SimpleNamespace(GoogleDriveClient=FakeClient)
        raise AssertionError(f"Unexpected import: {module_name}")

    with patch(
        "app.modules.connectors.infrastructure.adapters.lemma_connector_factory.importlib.import_module",
        side_effect=fake_import_module,
    ):
        client = create_lemma_execution_client(
            "google_drive",
            {"access_token": "access-token", "token_type": "Bearer"},
        )

    assert imported_modules == [
        "lemma_connectors.google_drive.client",
        "lemma_connectors.core.auth",
    ]
    assert client is not None


def test_resolve_lemma_package_name_maps_composio_google_ids_to_native_packages():
    assert resolve_lemma_package_name("googlecalendar") == "google_calendar"
    assert resolve_lemma_package_name("googledrive") == "google_drive"
    assert resolve_lemma_package_name("googledocs") == "google_docs"
    assert resolve_lemma_package_name("googlesheets") == "google_sheets"
