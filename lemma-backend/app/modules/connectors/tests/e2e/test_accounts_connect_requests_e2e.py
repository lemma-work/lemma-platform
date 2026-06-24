from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider

# A native Gmail row as seeded by the catalog importer: a LEMMA OAuth2 capability
# with NO stored oauth2_defaults. The Google OAuth endpoints/scopes are resolved
# at runtime from the code registry, so these tests prove the connect flow works
# without anything OAuth-static living in the DB.
GMAIL_NATIVE_CAPABILITIES = [
    {
        "provider": "LEMMA",
        "auth_scheme": "OAUTH2",
        "supports_org_custom_oauth": True,
    }
]
GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"


@pytest.mark.asyncio
async def test_connect_request_and_accounts_lifecycle(
    authenticated_client,
    fixed_test_user,
    fixed_test_org,
    db_session,
    monkeypatch,
):
    connector_id = f"oauth-app-{uuid4().hex[:8]}"
    app = Connector(
        id=connector_id,
            title="OAuth App",
            description="OAuth test app",
            provider_capabilities=[
                {
                    "provider": "LEMMA",
                    "auth_scheme": "OAUTH2",
                    "supports_org_custom_oauth": True,
                    "oauth2_defaults": {
                        "default_scopes": ["openid"],
                        "authorization_url": "https://mock.example.com/auth",
                        "token_url": "https://mock.example.com/token",
                    },
                }
            ],
            is_active=True,
        )
    db_session.add(app)
    await db_session.commit()

    org_id = fixed_test_org["id"]
    auth_config_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/auth-configs",
        json={
            "connector_id": connector_id,
            "provider": "LEMMA",
            "config_source": "ORG_CUSTOM",
            "credential_config": {
                "oauth2_credentials": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                }
            },
        },
    )
    assert auth_config_response.status_code == 200, auth_config_response.text
    auth_config = auth_config_response.json()
    assert (
        auth_config["credential_config"]["oauth2_credentials"]["client_secret"]
        == "********"
    )

    async def _fake_get_authorization_url(self, connector, user_id, state, redirect_uri):
        assert connector.oauth2_config.client_secret == "client-secret"
        return ("https://mock.example.com/authorize", "provider_state")

    async def _fake_exchange_code_for_credentials(
        self, connector, redirect_uri, user_id, state=None
    ):
        return OAuthCredentials(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )

    monkeypatch.setattr(
        LemmaAuthProvider,
        "get_authorization_url",
        _fake_get_authorization_url,
    )
    monkeypatch.setattr(
        LemmaAuthProvider,
        "exchange_code_for_credentials",
        _fake_exchange_code_for_credentials,
    )

    response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/connect-requests",
        json={"connector_id": connector_id},
    )
    assert response.status_code == 200, response.text
    connect_request = response.json()
    state = connect_request["attributes"]["state"]

    response = await authenticated_client.get(
        "/connectors/connect-requests/oauth/callback",
        params={"state": state, "code": "abc", "format": "json"},
    )
    assert response.status_code == 200, response.text
    account = response.json()
    account_id = account["id"]
    assert account["connector_id"] == connector_id

    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/accounts"
    )
    assert response.status_code == 200
    data = response.json()
    assert any(item["id"] == account_id for item in data["items"])

    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/accounts/{account_id}"
    )
    assert response.status_code == 200
    assert response.json()["id"] == account_id

    response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/accounts/{account_id}/credentials"
    )
    assert response.status_code == 200
    assert response.json()["data"]["access_token"] == "access-token"
    assert response.json()["data"]["expires_at"] is not None

    result = await db_session.execute(
        Account.__table__.select().where(Account.id == UUID(account_id))
    )
    stored_account = result.mappings().one()
    assert stored_account["credentials"]["_encrypted"] == "lemma-secret-v2"
    assert "access-token" not in str(stored_account["credentials"])

    result = await db_session.execute(
        AuthConfig.__table__.select().where(AuthConfig.id == UUID(auth_config["id"]))
    )
    stored_auth_config = result.mappings().one()
    assert stored_auth_config["provider_config"]["_encrypted"] == "lemma-secret-v2"
    assert "client-secret" not in str(stored_auth_config["provider_config"])

    response = await authenticated_client.delete(
        f"/organizations/{org_id}/connectors/auth-configs/{connector_id}"
    )
    assert response.status_code == 200

    result = await db_session.execute(
        Account.__table__.select().where(Account.id == UUID(account_id))
    )
    assert result.mappings().first() is None
    result = await db_session.execute(
        AuthConfig.__table__.select().where(AuthConfig.id == UUID(auth_config["id"]))
    )
    assert result.mappings().first() is None


@pytest.mark.asyncio
async def test_oauth_callback_requires_state(authenticated_client):
    response = await authenticated_client.get(
        "/connectors/connect-requests/oauth/callback",
        params={"format": "json"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "CONNECT_REQUEST_STATE_REQUIRED"


@pytest.mark.asyncio
async def test_lemma_system_default_requires_configured_env_credentials(
    authenticated_client,
    fixed_test_org,
    db_session,
    monkeypatch,
):
    connector_id = f"system-default-app-{uuid4().hex[:8]}"
    client_id_env = f"TEST_{connector_id.upper().replace('-', '_')}_CLIENT_ID"
    client_secret_env = f"TEST_{connector_id.upper().replace('-', '_')}_CLIENT_SECRET"
    monkeypatch.delenv(client_id_env, raising=False)
    monkeypatch.delenv(client_secret_env, raising=False)

    app = Connector(
        id=connector_id,
            title="System Default OAuth App",
            description="System default OAuth test app",
            provider_capabilities=[
                {
                    "provider": "LEMMA",
                    "auth_scheme": "OAUTH2",
                    "supports_org_custom_oauth": True,
                    "oauth2_defaults": {
                        "default_scopes": ["openid"],
                        "authorization_url": "https://mock.example.com/auth",
                    "token_url": "https://mock.example.com/token",
                },
                "system_oauth": {
                        "client_id_env": client_id_env,
                        "client_secret_env": client_secret_env,
                    },
                }
            ],
            is_active=True,
        )
    db_session.add(app)
    await db_session.commit()

    app_response = await authenticated_client.get(
        f"/connectors/{connector_id}"
    )
    assert app_response.status_code == 200, app_response.text
    lemma_capability = app_response.json()["provider_capabilities"][0]
    assert lemma_capability["system_default_available"] is False
    assert lemma_capability["supports_org_custom_oauth"] is True
    assert lemma_capability["auth_config_schema"] == {
        "type": "object",
        "required": ["client_id", "client_secret"],
        "properties": {
            "client_id": {"type": "string", "title": "Client ID"},
            "client_secret": {
                "type": "string",
                "title": "Client secret",
                "format": "password",
            },
        },
        "additionalProperties": False,
    }
    assert "supports_system_default" not in lemma_capability
    assert "requires_org_custom_credentials" not in lemma_capability
    assert "system_oauth" not in lemma_capability

    org_id = fixed_test_org["id"]
    response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/auth-configs",
        json={
            "connector_id": connector_id,
            "provider": "LEMMA",
            "config_source": "SYSTEM_DEFAULT",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "CONNECTOR_VALIDATION_ERROR"

    monkeypatch.setenv(client_id_env, "system-client-id")
    monkeypatch.setenv(client_secret_env, "system-client-secret")

    app_response = await authenticated_client.get(
        f"/connectors/{connector_id}"
    )
    assert app_response.status_code == 200, app_response.text
    lemma_capability = app_response.json()["provider_capabilities"][0]
    assert lemma_capability["system_default_available"] is True
    assert lemma_capability["supports_org_custom_oauth"] is True
    assert "supports_system_default" not in lemma_capability
    assert "requires_org_custom_credentials" not in lemma_capability
    assert "system_oauth" not in lemma_capability

    response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/auth-configs",
        json={
            "connector_id": connector_id,
            "provider": "LEMMA",
            "config_source": "SYSTEM_DEFAULT",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["credential_config"] is None


@pytest.mark.asyncio
async def test_direct_credential_managed_account_create_encrypts_credentials(
    authenticated_client,
    fixed_test_org,
    db_session,
):
    connector_id = f"surface-api-{uuid4().hex[:8]}"
    app = Connector(
        id=connector_id,
        title="Surface API App",
        description="Credential-managed surface app",
        provider_capabilities=[
            {
                "provider": "LEMMA",
                "auth_scheme": "API_KEY",
                "credential_schema": {
                    "type": "object",
                    "required": ["bot_token"],
                    "properties": {
                        "bot_token": {"type": "string", "format": "password"}
                    },
                },
            }
        ],
        is_active=True,
    )
    db_session.add(app)
    await db_session.commit()

    org_id = fixed_test_org["id"]
    auth_config_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/auth-configs",
        json={
            "connector_id": connector_id,
            "provider": "LEMMA",
            "config_source": "ORG_CUSTOM",
            "name": connector_id,
        },
    )
    assert auth_config_response.status_code == 200, auth_config_response.text

    connect_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/connect-requests",
        json={"connector_id": connector_id},
    )
    assert connect_response.status_code == 400
    assert connect_response.json()["code"] == "CONNECTOR_VALIDATION_ERROR"

    response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/accounts",
        json={
            "auth_config_name": connector_id,
            "credentials": {
                "bot_token": "telegram-secret-token",
                "api_base_url": "https://telegram.example.test/bot",
            },
            "provider_account_id": "bot-123",
            "email": "surface@example.test",
        },
    )
    assert response.status_code == 200, response.text
    account = response.json()
    account_id = account["id"]
    assert account["connector_id"] == connector_id
    assert account["provider_account_id"] == "bot-123"
    assert "credentials" not in account

    result = await db_session.execute(
        Account.__table__.select().where(Account.id == UUID(account_id))
    )
    stored_account = result.mappings().one()
    assert stored_account["credentials"]["_encrypted"] == "lemma-secret-v2"
    assert "telegram-secret-token" not in str(stored_account["credentials"])

    duplicate_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/accounts",
        json={
            "auth_config_name": connector_id,
            "credentials": {"bot_token": "another-secret"},
        },
    )
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["code"] == "ACCOUNT_ALREADY_CONNECTED"


@pytest.mark.asyncio
async def test_oauth_callback_renders_html_for_browser(authenticated_client):
    response = await authenticated_client.get(
        "/connectors/connect-requests/oauth/callback"
    )
    assert response.status_code == 400
    assert "text/html" in response.headers["content-type"]
    assert "We could not connect your account" in response.text
    assert "State parameter is required" in response.text


@pytest.mark.asyncio
async def test_list_accounts_uses_id_cursor_pagination(
    authenticated_client,
    fixed_test_user,
    fixed_test_org,
    db_session,
):
    connector_ids = [f"accounts-page-{index}-{uuid4().hex[:6]}" for index in range(3)]
    org_id = UUID(fixed_test_org["id"])

    for connector_id in connector_ids:
        app = Connector(
                id=connector_id,
                title=f"App {connector_id}",
                description="Pagination test app",
                provider_capabilities=[
                    {"provider": "LEMMA", "auth_scheme": "OAUTH2"}
                ],
                is_active=True,
            )
        db_session.add(app)
        await db_session.flush()
        auth_config = AuthConfig(
            organization_id=org_id,
            connector_id=connector_id,
            provider=AuthProvider.LEMMA.value,
            config_source="SYSTEM_DEFAULT",
            status="ACTIVE",
            name=connector_id,
        )
        db_session.add(auth_config)
        await db_session.flush()
        db_session.add(
            Account(
                user_id=fixed_test_user["id"],
                organization_id=org_id,
                auth_config_id=auth_config.id,
                connector_id=connector_id,
                credentials={"access_token": connector_id},
            )
        )

    await db_session.commit()

    first_page = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/accounts",
        params={"limit": 2},
    )
    assert first_page.status_code == 200, first_page.text
    first_payload = first_page.json()
    assert len(first_payload["items"]) == 2
    assert first_payload["next_page_token"] is not None

    first_ids = [UUID(item["id"]) for item in first_payload["items"]]
    assert first_payload["next_page_token"] == str(first_ids[-1])

    second_page = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/accounts",
        params={"limit": 2, "page_token": first_payload["next_page_token"]},
    )
    assert second_page.status_code == 200, second_page.text
    second_payload = second_page.json()
    second_ids = [UUID(item["id"]) for item in second_payload["items"]]

    assert first_ids[0] < first_ids[1]
    assert all(account_id > first_ids[-1] for account_id in second_ids)


@pytest.mark.asyncio
async def test_gmail_org_custom_connect_request_builds_google_authorization_url(
    authenticated_client,
    fixed_test_org,
    db_session,
):
    """Native Gmail must be connectable with an org's own Google OAuth client.

    Regression for "OAuth2 defaults are not configured for 'gmail'.": the Google
    OAuth endpoints/scopes are resolved at runtime from the code registry (the DB
    row stores none), so combining them with org-custom credentials yields a real
    Google authorization URL instead of a 400.
    """
    app = Connector(
        id="gmail",
        title="Gmail",
        description="Native Gmail connector",
        provider_capabilities=GMAIL_NATIVE_CAPABILITIES,
        is_active=True,
    )
    db_session.add(app)
    await db_session.commit()

    org_id = fixed_test_org["id"]
    auth_config_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/auth-configs",
        json={
            "connector_id": "gmail",
            "provider": "LEMMA",
            "config_source": "ORG_CUSTOM",
            "credential_config": {
                "oauth2_credentials": {
                    "client_id": "org-google-client-id",
                    "client_secret": "org-google-client-secret",
                }
            },
        },
    )
    assert auth_config_response.status_code == 200, auth_config_response.text
    auth_config_id = auth_config_response.json()["id"]

    # Mirror the exact payload the frontend sends.
    response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/connect-requests",
        json={"auth_config_id": auth_config_id},
    )
    assert response.status_code == 200, response.text
    authorization_url = response.json()["authorization_url"]
    assert authorization_url.startswith(GOOGLE_AUTHORIZATION_URL)
    # Uses the org's stored client id, requests the Gmail scope, and asks for an
    # offline refresh token.
    assert "client_id=org-google-client-id" in authorization_url
    assert "gmail.modify" in authorization_url
    assert "access_type=offline" in authorization_url


@pytest.mark.asyncio
async def test_gmail_system_default_connect_request_uses_env_google_client(
    authenticated_client,
    fixed_test_org,
    db_session,
    monkeypatch,
):
    """Native Gmail must also be connectable with the system Google OAuth client.

    When an org picks config_source=SYSTEM_DEFAULT for the Lemma provider, the
    backend resolves GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET from env and combines
    them with the registry OAuth defaults to build the Google authorization URL.
    """
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "system-google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "system-google-client-secret")

    app = Connector(
        id="gmail",
        title="Gmail",
        description="Native Gmail connector",
        provider_capabilities=GMAIL_NATIVE_CAPABILITIES,
        is_active=True,
    )
    db_session.add(app)
    await db_session.commit()

    org_id = fixed_test_org["id"]
    auth_config_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/auth-configs",
        json={
            "connector_id": "gmail",
            "provider": "LEMMA",
            "config_source": "SYSTEM_DEFAULT",
        },
    )
    assert auth_config_response.status_code == 200, auth_config_response.text
    auth_config_id = auth_config_response.json()["id"]

    response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/connect-requests",
        json={"auth_config_id": auth_config_id},
    )
    assert response.status_code == 200, response.text
    authorization_url = response.json()["authorization_url"]
    assert authorization_url.startswith(GOOGLE_AUTHORIZATION_URL)
    assert "client_id=system-google-client-id" in authorization_url
    assert "gmail.modify" in authorization_url
    assert "access_type=offline" in authorization_url


@pytest.mark.asyncio
async def test_gmail_connector_api_reflects_runtime_oauth_resolution(
    authenticated_client,
    db_session,
    monkeypatch,
):
    """The connector API resolves OAuth defaults + system availability live.

    system_default_available follows GOOGLE_CLIENT_ID/SECRET env presence on each
    request (not a stale DB value), and the registry oauth2_defaults are surfaced
    even though the row stores none.
    """
    app = Connector(
        id="gmail",
        title="Gmail",
        description="Native Gmail connector",
        provider_capabilities=GMAIL_NATIVE_CAPABILITIES,
        is_active=True,
    )
    db_session.add(app)
    await db_session.commit()

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    response = await authenticated_client.get("/connectors/gmail")
    assert response.status_code == 200, response.text
    capability = response.json()["provider_capabilities"][0]
    assert capability["supports_org_custom_oauth"] is True
    assert capability["system_default_available"] is False
    # Registry endpoints/scopes are surfaced despite nothing stored on the row.
    assert capability["oauth2_defaults"]["authorization_url"] == (
        GOOGLE_AUTHORIZATION_URL
    )
    assert "system_oauth" not in capability

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "system-google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "system-google-client-secret")
    response = await authenticated_client.get("/connectors/gmail")
    assert response.status_code == 200, response.text
    capability = response.json()["provider_capabilities"][0]
    assert capability["system_default_available"] is True
