"""Real Composio e2e tests for the connector connect/execute/status flows.

These hit the live Composio API and are gated on real credentials, so they are
skipped by default (CI). Three tiers:

* **Tier 1 (``provider`` marker, no browser):** API-key connect + operation
  (needs ``COMPOSIO_API_KEY`` + ``TEST_OPENWEATHER_API_KEY``) and the
  reauth-flip-on-bad-credential path (needs only ``COMPOSIO_API_KEY``).
* **Tier 2 (``provider`` + ``human`` markers, opt-in):** real OAuth consent in a
  browser, the Canva exchange fix, and in-place reconnect. Opt in with
  ``RUN_HUMAN_OAUTH=1``; a human completes consent while the test polls Composio.
* **Webhook (``provider`` marker):** Composio webhook signature verification with
  ``COMPOSIO_WEBHOOK_SECRET`` (local crypto, no network).

Run examples::

    # Tier 1 reauth (only needs the Composio platform key)
    pytest -m provider app/modules/connectors/tests/e2e/test_composio_real_e2e.py \
        -k reauth -s

    # Human OAuth (opens your browser; you consent live)
    RUN_HUMAN_OAUTH=1 pytest -m "provider and human" \
        app/modules/connectors/tests/e2e/test_composio_real_e2e.py -s
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from uuid import uuid4

import pytest
from composio import Composio
from httpx import AsyncClient
from sqlalchemy import delete

sys.path.append(str(Path(__file__).resolve().parents[5]))

from app.modules.connectors.config import connector_settings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.domain.auth_config import AuthConfigSource
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.connectors.infrastructure.models.connector_trigger import (
    ConnectorTrigger,
)
from app.modules.connectors.infrastructure.repositories.connector_operation_repository import (
    ConnectorOperationRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_repository import (
    ConnectorRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_trigger_repository import (
    ConnectorTriggerRepository,
)

# --- load the import script as a module (real catalog sync helpers) -----------
_IMPORTER_PATH = Path(__file__).resolve().parents[5] / "scripts" / "import_connector_catalog.py"
_IMPORTER_SPEC = importlib.util.spec_from_file_location("import_connector_catalog", _IMPORTER_PATH)
assert _IMPORTER_SPEC and _IMPORTER_SPEC.loader
importer = importlib.util.module_from_spec(_IMPORTER_SPEC)
_IMPORTER_SPEC.loader.exec_module(importer)

OPENWEATHER_SLUG = "openweather_api"
OPENWEATHER_OP = "OPENWEATHER_API_GET_CURRENT_WEATHER"


def _env_value(name: str) -> str | None:
    """Read an env var, falling back to lemma-backend/.env."""
    value = os.getenv(name)
    if value:
        return value
    env_path = Path(__file__).resolve().parents[5] / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if key.strip() == name:
            return raw.strip().strip('"').strip("'")
    return None


def _composio_api_key() -> str | None:
    return connector_settings.composio_api_key or _env_value("COMPOSIO_API_KEY")


def _require_composio() -> str:
    key = _composio_api_key()
    if not key:
        pytest.skip("Real Composio e2e requires COMPOSIO_API_KEY.")
    return key


def _composio_client() -> Composio:
    return Composio(api_key=_require_composio())


async def _reseed_composio_app(db_session, connector_id: str) -> None:
    """Re-import a single Composio app's catalog (connector + ops + capability)."""
    await db_session.execute(
        delete(ConnectorTrigger).where(ConnectorTrigger.connector_id == connector_id)
    )
    await db_session.execute(
        delete(ConnectorOperation).where(ConnectorOperation.connector_id == connector_id)
    )
    await db_session.execute(delete(Connector).where(Connector.id == connector_id))
    await db_session.commit()

    uow = SqlAlchemyUnitOfWork(db_session)
    await importer._sync_composio_catalog(
        ConnectorRepository(uow),
        ConnectorOperationRepository(uow),
        ConnectorTriggerRepository(uow),
        app_filters={connector_id},
        managed_by="composio",
        page_size=100,
        max_composio_apps=10,
    )
    await uow.commit()


async def _seed_composio_auth_config(db_session, connector_id: str, org_id) -> AuthConfig:
    auth_config = AuthConfig(
        organization_id=org_id,
        connector_id=connector_id,
        provider=AuthProvider.COMPOSIO.value,
        config_source=AuthConfigSource.SYSTEM_DEFAULT.value,
        name=f"{connector_id}-{uuid4().hex[:8]}",
    )
    db_session.add(auth_config)
    await db_session.flush()
    await db_session.commit()
    return auth_config


def _cleanup_user_accounts(user_id) -> None:
    """Best-effort: delete every Composio connected account for the test user."""
    try:
        composio = Composio(api_key=_composio_api_key() or "")
        accounts = composio.connected_accounts.list(user_ids=[str(user_id)])
        for item in getattr(accounts, "items", []) or []:
            try:
                composio.connected_accounts.delete(item.id)
            except Exception:
                pass
    except Exception:
        pass


# =============================================================================
# Tier 1a — API-key connect + real operation (needs a real OpenWeather key)
# =============================================================================
@pytest.mark.provider
@pytest.mark.asyncio
async def test_composio_api_key_connect_and_execute(
    authenticated_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    db_session,
):
    _require_composio()
    api_key = _env_value("TEST_OPENWEATHER_API_KEY")
    if not api_key:
        pytest.skip("Real OpenWeather connect requires TEST_OPENWEATHER_API_KEY.")

    org_id = fixed_test_org["id"]
    await _reseed_composio_app(db_session, OPENWEATHER_SLUG)

    # The import populated the API-key credential schema (generic_api_key).
    connector = await db_session.get(Connector, OPENWEATHER_SLUG)
    assert connector is not None
    capability = connector.to_entity().capability_for(AuthProvider.COMPOSIO)
    assert capability.auth_config_schema is not None
    assert "generic_api_key" in capability.auth_config_schema["properties"]

    auth_config = await _seed_composio_auth_config(db_session, OPENWEATHER_SLUG, org_id)
    accounts_url = f"/organizations/{org_id}/connectors/accounts"

    try:
        resp = await authenticated_client.post(
            accounts_url,
            json={
                "auth_config_id": str(auth_config.id),
                "credentials": {"generic_api_key": api_key},
            },
        )
        assert resp.status_code == 200, resp.text
        account = resp.json()
        assert account["status"] == AccountStatus.CONNECTED.value
        account_id = account["id"]

        # A real connected account was created on Composio's side.
        creds_resp = await authenticated_client.get(
            f"{accounts_url}/{account_id}/credentials"
        )
        assert creds_resp.status_code == 200, creds_resp.text
        assert creds_resp.json()["data"].get("connection_id")

        ops_url = (
            f"/organizations/{org_id}/connectors/{auth_config.name}/operations/"
            f"{OPENWEATHER_OP}/execute"
        )
        exec_resp = await authenticated_client.post(
            ops_url,
            json={"payload": {"q": "London", "units": "metric"}, "account_id": account_id},
        )
        assert exec_resp.status_code == 200, exec_resp.text
        # OpenWeather echoes the resolved city in the response.
        assert "London" in json.dumps(exec_resp.json())
    finally:
        _cleanup_user_accounts(fixed_test_user["id"])


# =============================================================================
# Tier 1b — account auto-flips to REAUTH_REQUIRED on a provider auth failure
# =============================================================================
@pytest.mark.provider
@pytest.mark.asyncio
async def test_composio_account_flips_to_reauth_on_unauthorized(
    authenticated_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    db_session,
):
    _require_composio()
    org_id = fixed_test_org["id"]
    await _reseed_composio_app(db_session, OPENWEATHER_SLUG)
    auth_config = await _seed_composio_auth_config(db_session, OPENWEATHER_SLUG, org_id)
    accounts_url = f"/organizations/{org_id}/connectors/accounts"

    try:
        resp = await authenticated_client.post(
            accounts_url,
            json={
                "auth_config_id": str(auth_config.id),
                "credentials": {"generic_api_key": "deliberately-invalid-key"},
            },
        )
        if resp.status_code != 200:
            pytest.skip(
                "Composio rejected the bad API key at connect time; the "
                f"execute-time reauth path is not reachable for this app: {resp.text}"
            )
        account_id = resp.json()["id"]

        ops_url = (
            f"/organizations/{org_id}/connectors/{auth_config.name}/operations/"
            f"{OPENWEATHER_OP}/execute"
        )
        exec_resp = await authenticated_client.post(
            ops_url,
            json={"payload": {"q": "London"}, "account_id": account_id},
        )
        # The provider rejects the bad key; our API surfaces an auth failure.
        assert exec_resp.status_code in (401, 403), exec_resp.text

        # ...and the account is auto-flagged for re-authentication.
        get_resp = await authenticated_client.get(f"{accounts_url}/{account_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["status"] == AccountStatus.REAUTH_REQUIRED.value
    finally:
        _cleanup_user_accounts(fixed_test_user["id"])


# =============================================================================
# Tier 2 — human-orchestrated real OAuth: connect (Canva fix) + reconnect
# =============================================================================
def _human_oauth_enabled() -> bool:
    return bool(os.getenv("RUN_HUMAN_OAUTH"))


# Read-only "smoke" operations per OAuth app, to prove the connected account
# actually works for real execution. (slug, payload).
_SMOKE_OPS: dict[str, tuple[str, dict]] = {
    "google_calendar": ("GOOGLECALENDAR_LIST_CALENDARS", {}),
    "gmail": ("GMAIL_GET_PROFILE", {}),
}


def _wait_for_active_connection(composio: Composio, connection_id: str, timeout: float = 300.0):
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        try:
            account = composio.connected_accounts.get(connection_id)
            last_status = getattr(account, "status", None)
            if str(last_status).upper() == "ACTIVE":
                return account
            if str(last_status).upper() in {"FAILED", "EXPIRED", "REVOKED"}:
                pytest.fail(f"Connection {connection_id} entered terminal state {last_status}")
        except Exception:
            pass
        time.sleep(3)
    pytest.fail(f"Timed out waiting for {connection_id} to become ACTIVE (last={last_status})")


@pytest.mark.provider
@pytest.mark.human
@pytest.mark.timeout(900)
@pytest.mark.asyncio
async def test_composio_oauth_connect_and_reconnect_human(
    authenticated_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    db_session,
):
    if not _human_oauth_enabled():
        pytest.skip("Set RUN_HUMAN_OAUTH=1 to run the human-in-the-loop OAuth test.")
    composio = _composio_client()

    org_id = fixed_test_org["id"]
    app = os.getenv("TEST_OAUTH_APP", "google_calendar")
    await _reseed_composio_app(db_session, app)
    auth_config = await _seed_composio_auth_config(db_session, app, org_id)
    cr_url = f"/organizations/{org_id}/connectors/connect-requests"
    callback_url = "/connectors/connect-requests/oauth/callback"

    async def _run_smoke_op(account_id: str) -> None:
        smoke = _SMOKE_OPS.get(app)
        if not smoke:
            return
        op_name, payload = smoke
        exec_resp = await authenticated_client.post(
            f"/organizations/{org_id}/connectors/{auth_config.name}/operations/"
            f"{op_name}/execute",
            json={"payload": payload, "account_id": account_id},
        )
        assert exec_resp.status_code == 200, exec_resp.text
        print(f"\n=== {op_name} succeeded: {json.dumps(exec_resp.json())[:300]} ===\n")

    async def _initiate() -> tuple[str, str, str]:
        """POST a connect request; return (state, connection_id, authorization_url)."""
        resp = await authenticated_client.post(
            cr_url, json={"auth_config_id": str(auth_config.id)}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        attributes = body["attributes"] or {}
        return attributes["state"], attributes["provider_state"], body["authorization_url"]

    async def _complete(state: str, connection_id: str) -> dict:
        # Drive our real callback in-process (the e2e app is ASGI, not on a
        # reachable port) — this exercises exchange_code_for_credentials for real.
        cb = await authenticated_client.get(
            callback_url,
            params={"state": state, "connectedAccountId": connection_id, "format": "json"},
        )
        assert cb.status_code == 200, cb.text
        return cb.json()

    try:
        # --- First connect (the only human consent needed) ------------------
        state, connection_id, authorization_url = await _initiate()
        print(f"\n\n=== HUMAN ACTION REQUIRED: authorize {app} in your browser ===")
        print(authorization_url)
        print("Waiting for you to complete consent...\n")
        try:
            webbrowser.open(authorization_url)
        except Exception:
            pass

        _wait_for_active_connection(composio, connection_id)
        account = await _complete(state, connection_id)
        # The Canva fix: succeeds even when no raw access_token is surfaced.
        assert account["status"] == AccountStatus.CONNECTED.value
        original_account_id = account["id"]

        # The connected account works for a real operation.
        await _run_smoke_op(original_account_id)

        # --- Reconnect on the same account_id (no second consent) -----------
        # Mark the account unusable, then re-initiate: this must be ALLOWED
        # (no 409). We complete the callback by reusing the still-active first
        # connection, so no second browser consent is required.
        row = await db_session.get(Account, original_account_id)
        row.status = AccountStatus.REAUTH_REQUIRED.value
        await db_session.commit()

        reconnect_state, _new_connection_id, _ = await _initiate()  # 200 == allowed
        reconnect = await _complete(reconnect_state, connection_id)
        # Must reuse the SAME account_id (preserving downstream references).
        assert reconnect["id"] == original_account_id
        assert reconnect["status"] == AccountStatus.CONNECTED.value

        # The reconnected account still works.
        await _run_smoke_op(original_account_id)
    finally:
        _cleanup_user_accounts(fixed_test_user["id"])


# =============================================================================
# Webhook — Composio webhook signature verification
# =============================================================================
@pytest.mark.provider
def test_composio_webhook_signature_verification():
    secret = connector_settings.composio_webhook_secret or _env_value("COMPOSIO_WEBHOOK_SECRET")
    if not secret:
        pytest.skip("Webhook verification requires COMPOSIO_WEBHOOK_SECRET.")

    from app.modules.schedule.infrastructure.adapters.composio_webhook_verifier import (
        ComposioWebhookVerifier,
    )

    payload = json.dumps(
        {
            "trigger_name": "GMAIL_NEW_GMAIL_MESSAGE",
            "connection_id": "ca_test_connection",
            "trigger_id": "ti_test_trigger",
            "payload": {"message_id": "m1", "subject": "hello"},
            "log_id": "log_test",
        }
    )
    webhook_id = "msg_test_123"
    timestamp = str(int(time.time()))
    to_sign = f"{webhook_id}.{timestamp}.{payload}"
    digest = hmac.new(secret.encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha256).digest()
    signature = "v1," + base64.b64encode(digest).decode("utf-8")

    headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": signature,
    }

    verifier = ComposioWebhookVerifier()
    result = verifier.verify(payload, headers)
    assert result["raw_payload"]["connection_id"] == "ca_test_connection"

    # A tampered signature is rejected.
    bad_headers = {**headers, "webhook-signature": "v1," + base64.b64encode(b"wrong").decode()}
    with pytest.raises(Exception):
        verifier.verify(payload, bad_headers)
