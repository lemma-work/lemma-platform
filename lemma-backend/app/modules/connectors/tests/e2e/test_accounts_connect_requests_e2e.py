from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

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


async def _create_pod(owner_client, org_id: str, name: str) -> str:
    response = await owner_client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"{name} {uuid4().hex[:8]}",
            "description": "connectors authz e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _default_pod_agent_headers(*, user_id: str, pod_id: str) -> dict[str, str]:
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=DEFAULT_POD_AGENT_ID,
        workload_name=DEFAULT_POD_AGENT_NAME,
        pod_id=UUID(pod_id),
        session_id=f"connectors-authz-e2e-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


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

    async def _fake_get_authorization_url(
        self, connector, user_id, state, redirect_uri
    ):
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
    # Raw credentials are deliberately an internal-only connector contract.
    # Keep this assertion so a future route registration cannot accidentally
    # re-expose access tokens through the public API.
    assert response.status_code == 404

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

    app_response = await authenticated_client.get(f"/connectors/{connector_id}")
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

    app_response = await authenticated_client.get(f"/connectors/{connector_id}")
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
    # The first account connected for an auth config is the default.
    assert account["is_default"] is True

    # Multiple credential-managed accounts per auth config are allowed (e.g.
    # several bot tokens); a subsequent one succeeds and is not the default.
    second_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/accounts",
        json={
            "auth_config_name": connector_id,
            "credentials": {"bot_token": "another-secret"},
        },
    )
    assert second_response.status_code == 200, second_response.text
    second_account = second_response.json()
    assert second_account["id"] != account_id
    assert second_account["is_default"] is False


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
            provider_capabilities=[{"provider": "LEMMA", "auth_scheme": "OAUTH2"}],
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


@pytest.mark.asyncio
async def test_delete_account_removes_account_and_404s_on_repeat(
    authenticated_client,
    fixed_test_org,
    db_session,
):
    """Regression: DELETE .../connectors/accounts/{account_id} previously 400'd
    with "organization_id is required". The route's `reject_delegated_workload`
    dependency resolves org context via `get_org_context`, which only read the
    `{org_id}` path param while this router is mounted under
    `{organization_id}` -- so the org id was never found. Exercised end-to-end
    (real HTTP + real dependency chain) rather than via the service directly,
    since the bug lived in path-param resolution, not the service layer.
    """
    connector_id = f"delete-app-{uuid4().hex[:8]}"
    app = Connector(
        id=connector_id,
        title="Delete Test App",
        description="Credential-managed delete test app",
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

    create_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/accounts",
        json={
            "auth_config_name": connector_id,
            "credentials": {"bot_token": "delete-me-token"},
        },
    )
    assert create_response.status_code == 200, create_response.text
    account_id = create_response.json()["id"]

    delete_response = await authenticated_client.delete(
        f"/organizations/{org_id}/connectors/accounts/{account_id}"
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["success"] is True

    result = await db_session.execute(
        Account.__table__.select().where(Account.id == UUID(account_id))
    )
    assert result.mappings().first() is None

    # Deleting again 404s (account not found) rather than 400ing on org context
    # resolution, confirming the org id is actually being read from the path.
    second_delete = await authenticated_client.delete(
        f"/organizations/{org_id}/connectors/accounts/{account_id}"
    )
    assert second_delete.status_code == 404, second_delete.text
    assert second_delete.json()["code"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_credential_managed_account_rejects_duplicate_identity_and_exposes_display_name(
    authenticated_client,
    fixed_test_org,
    db_session,
):
    """The same provider identity can't be connected twice, and the account
    response carries a ``display_name`` field for the UI."""
    connector_id = f"dedup-app-{uuid4().hex[:8]}"
    app = Connector(
        id=connector_id,
        title="Dedup App",
        description="Credential-managed dedup test app",
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

    # First connect for identity "acc-alpha".
    first = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/accounts",
        json={
            "auth_config_name": connector_id,
            "credentials": {"bot_token": "tok-1"},
            "provider_account_id": "acc-alpha",
        },
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["provider_account_id"] == "acc-alpha"
    assert "display_name" in body  # field is exposed to the UI

    # Same identity again → rejected (not silently duplicated).
    dup = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/accounts",
        json={
            "auth_config_name": connector_id,
            "credentials": {"bot_token": "tok-2"},
            "provider_account_id": "acc-alpha",
        },
    )
    assert dup.status_code == 409, dup.text
    assert dup.json()["code"] == "ACCOUNT_ALREADY_CONNECTED"

    # A different identity under the same auth config is still allowed.
    other = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/accounts",
        json={
            "auth_config_name": connector_id,
            "credentials": {"bot_token": "tok-3"},
            "provider_account_id": "acc-beta",
        },
    )
    assert other.status_code == 200, other.text
    assert other.json()["provider_account_id"] == "acc-beta"


@pytest.mark.asyncio
async def test_oauth_new_account_addition_and_reauth_flows(
    authenticated_client,
    fixed_test_org,
    db_session,
    monkeypatch,
):
    """End-to-end coverage for multi-account OAuth via the accounts API:

    * connecting a second, distinct provider identity creates a NEW account
      (not a duplicate/clobber of the first) and is not the default;
    * re-authing an identity that already has an account (matched by
      provider_account_id) updates that SAME account in place -- restoring it
      to CONNECTED -- instead of creating a third account.
    """
    connector_id = f"oauth-multi-app-{uuid4().hex[:8]}"
    app = Connector(
        id=connector_id,
        title="OAuth Multi App",
        description="OAuth multi-account test app",
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

    async def _fake_get_authorization_url(
        self, connector, user_id, state, redirect_uri
    ):
        return ("https://mock.example.com/authorize", "provider_state")

    # The callback URL's "code" query param stands in for the provider's actual
    # authorization code; here it doubles as a way to pick which identity the
    # exchange returns, so the test can drive distinct-identity vs same-identity
    # callbacks without a real OAuth provider.
    async def _fake_exchange_code_for_credentials(
        self, connector, redirect_uri, user_id, state=None
    ):
        from urllib.parse import parse_qs, urlparse

        code = (parse_qs(urlparse(redirect_uri).query).get("code") or [""])[0]
        return OAuthCredentials(
            access_token=f"access-token-{code}",
            refresh_token=f"refresh-token-{code}",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            raw_response={"provider_account_id": code},
        )

    monkeypatch.setattr(
        LemmaAuthProvider, "get_authorization_url", _fake_get_authorization_url
    )
    monkeypatch.setattr(
        LemmaAuthProvider,
        "exchange_code_for_credentials",
        _fake_exchange_code_for_credentials,
    )

    async def _connect(identity_code: str) -> dict:
        response = await authenticated_client.post(
            f"/organizations/{org_id}/connectors/connect-requests",
            json={"connector_id": connector_id},
        )
        assert response.status_code == 200, response.text
        state = response.json()["attributes"]["state"]
        callback = await authenticated_client.get(
            "/connectors/connect-requests/oauth/callback",
            params={"state": state, "code": identity_code, "format": "json"},
        )
        assert callback.status_code == 200, callback.text
        return callback.json()

    # New account addition flow: a first identity connects and becomes default.
    first_account = await _connect("user-alpha")
    assert first_account["provider_account_id"] == "user-alpha"
    assert first_account["is_default"] is True
    assert first_account["status"] == "CONNECTED"

    # A second, distinct identity connects -> a NEW, non-default account.
    second_account = await _connect("user-beta")
    assert second_account["provider_account_id"] == "user-beta"
    assert second_account["id"] != first_account["id"]
    assert second_account["is_default"] is False
    assert second_account["status"] == "CONNECTED"

    list_response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/accounts",
        params={"connector_id": connector_id},
    )
    assert list_response.status_code == 200, list_response.text
    assert {item["id"] for item in list_response.json()["items"]} == {
        first_account["id"],
        second_account["id"],
    }

    # Simulate the first account degrading (e.g. token revoked upstream).
    result = await db_session.execute(
        Account.__table__.select().where(Account.id == UUID(first_account["id"]))
    )
    stored = result.mappings().one()
    await db_session.execute(
        Account.__table__.update()
        .where(Account.id == UUID(first_account["id"]))
        .values(status="REAUTH_REQUIRED")
    )
    await db_session.commit()
    assert stored["status"] == "CONNECTED"  # sanity: it really was healthy before

    # Reauth flow: re-connecting the SAME identity updates the SAME account in
    # place (no third account created) and restores it to CONNECTED.
    reauth_account = await _connect("user-alpha")
    assert reauth_account["id"] == first_account["id"]
    assert reauth_account["status"] == "CONNECTED"

    list_response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/accounts",
        params={"connector_id": connector_id},
    )
    assert list_response.status_code == 200, list_response.text
    accounts_after_reauth = list_response.json()["items"]
    assert len(accounts_after_reauth) == 2
    assert {item["id"] for item in accounts_after_reauth} == {
        first_account["id"],
        second_account["id"],
    }

    credentials_response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/accounts/{first_account['id']}/credentials"
    )
    assert credentials_response.status_code == 404, credentials_response.text


@pytest.mark.asyncio
async def test_list_and_get_auth_config(
    authenticated_client,
    fixed_test_org,
    db_session,
):
    """GET .../auth-configs (list) and GET .../auth-configs/{name} (single) had
    no e2e coverage even though create/delete did."""
    connector_id = f"read-auth-config-{uuid4().hex[:8]}"
    app = Connector(
        id=connector_id,
        title="Read Auth Config App",
        description="App for auth-config read coverage",
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
    create_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/auth-configs",
        json={
            "connector_id": connector_id,
            "provider": "LEMMA",
            "config_source": "ORG_CUSTOM",
            "name": connector_id,
        },
    )
    assert create_response.status_code == 200, create_response.text
    auth_config_id = create_response.json()["id"]

    list_response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/auth-configs"
    )
    assert list_response.status_code == 200, list_response.text
    assert any(item["id"] == auth_config_id for item in list_response.json()["items"])

    get_response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/auth-configs/{connector_id}"
    )
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["id"] == auth_config_id
    assert get_response.json()["connector_id"] == connector_id

    missing_response = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/auth-configs/does-not-exist-{uuid4().hex[:8]}"
    )
    assert missing_response.status_code == 404, missing_response.text


@pytest.mark.asyncio
async def test_default_pod_agent_cannot_delete_account(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """A delegated workload (the default pod agent) is denied outright on
    account deletion -- this is an org-level, ownership-based action a
    workload has no business performing on its own, not just a nuanced
    grant/approval gate."""
    connector_id = f"agent-delete-app-{uuid4().hex[:8]}"
    app = Connector(
        id=connector_id,
        title="Agent Delete Test App",
        description="Credential-managed agent-delete test app",
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

    create_response = await authenticated_client.post(
        f"/organizations/{org_id}/connectors/accounts",
        json={
            "auth_config_name": connector_id,
            "credentials": {"bot_token": "agent-delete-token"},
        },
    )
    assert create_response.status_code == 200, create_response.text
    account_id = create_response.json()["id"]

    pod_id = await _create_pod(authenticated_client, org_id, "Agent Delete Pod")
    agent_headers = await _default_pod_agent_headers(
        user_id=fixed_test_user["id"], pod_id=pod_id
    )

    response = await async_client.delete(
        f"/organizations/{org_id}/connectors/accounts/{account_id}",
        headers=agent_headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"

    # Control: the account is untouched and the human can still delete it.
    still_there = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/accounts/{account_id}"
    )
    assert still_there.status_code == 200, still_there.text


@pytest.mark.asyncio
async def test_default_pod_agent_cannot_delete_auth_config(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """Deleting an auth config cascades to delete every account under it for
    every user in the org -- at least as destructive as deleting a single
    account, so a delegated workload must be denied outright here too."""
    connector_id = f"agent-delete-config-{uuid4().hex[:8]}"
    app = Connector(
        id=connector_id,
        title="Agent Delete Config App",
        description="App for auth-config delegated-delete coverage",
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

    pod_id = await _create_pod(authenticated_client, org_id, "Agent Delete Config Pod")
    agent_headers = await _default_pod_agent_headers(
        user_id=fixed_test_user["id"], pod_id=pod_id
    )

    response = await async_client.delete(
        f"/organizations/{org_id}/connectors/auth-configs/{connector_id}",
        headers=agent_headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"

    # Control: the auth config is untouched and the human can still delete it.
    still_there = await authenticated_client.get(
        f"/organizations/{org_id}/connectors/auth-configs/{connector_id}"
    )
    assert still_there.status_code == 200, still_there.text
