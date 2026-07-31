"""Per-app account identity derivation (provider_account_id + email + display_name)."""

from __future__ import annotations

import pytest

import app.modules.connectors.services.account_identity as account_identity
from app.modules.connectors.services.account_identity import resolve_account_identity

pytestmark = pytest.mark.asyncio


async def test_gmail_identity_from_profile_email():
    identity = await resolve_account_identity(
        connector_id="gmail",
        credentials={},
        profile={"email_address": "rahul@gmail.com"},
    )
    assert identity.provider_account_id == "rahul@gmail.com"
    assert identity.email == "rahul@gmail.com"
    assert identity.display_name == "rahul@gmail.com"


async def test_gmail_identity_falls_back_to_sub_when_profile_has_no_email():
    """A narrow-scope grant (e.g. Drive-only) can leave the profile call
    without an email -- the account must still get a distinguishable label."""
    identity = await resolve_account_identity(
        connector_id="google_drive",
        credentials={"raw_response": {"sub": "109876543210"}},
        profile={},
    )
    assert identity.email is None
    assert identity.provider_account_id == "109876543210"
    assert identity.display_name == "109876543210"


async def test_slack_identity_uses_team_name_and_user_id():
    identity = await resolve_account_identity(
        connector_id="slack",
        credentials={
            "raw_response": {
                "team": {"id": "T123", "name": "Acme"},
                "authed_user": {"id": "U777"},
                "bot_user_id": "B999",
            }
        },
    )
    assert identity.provider_account_id == "U777"
    assert identity.display_name == "Acme"


async def test_slack_identity_falls_back_to_provider_account_id_without_team_name():
    identity = await resolve_account_identity(
        connector_id="slack",
        credentials={"raw_response": {"authed_user": {"id": "U777"}}},
    )
    assert identity.provider_account_id == "U777"
    assert identity.display_name == "U777"


async def test_whatsapp_identity_from_phone_number_id():
    identity = await resolve_account_identity(
        connector_id="whatsapp",
        credentials={"phone_number_id": "PN42", "display_phone_number": "+1 555 0100"},
    )
    assert identity.provider_account_id == "PN42"
    assert identity.display_name == "+1 555 0100"


async def test_resend_identity_from_address():
    identity = await resolve_account_identity(
        connector_id="resend",
        credentials={"from_address": "ops@pod.lemma.work"},
    )
    assert identity.provider_account_id == "ops@pod.lemma.work"
    assert identity.email == "ops@pod.lemma.work"
    assert identity.display_name == "ops@pod.lemma.work"


async def test_generic_identity_falls_back_to_raw_user_id():
    identity = await resolve_account_identity(
        connector_id="airtable",
        credentials={"raw_response": {"user": {"id": "acc-1"}}},
    )
    assert identity.provider_account_id == "acc-1"


async def test_generic_identity_falls_back_to_composio_word_id_when_no_email():
    """Most of the catalog (Composio OAuth apps with no dedicated handler) has
    no universal email/username field -- word_id is Composio's own
    toolkit-agnostic label built for exactly this: telling multiple accounts
    of the same app apart."""
    identity = await resolve_account_identity(
        connector_id="github",
        credentials={"raw_response": {"word_id": "github_red-castle"}},
    )
    assert identity.email is None
    assert identity.display_name == "github_red-castle"


async def test_generic_identity_prefers_alias_over_word_id():
    identity = await resolve_account_identity(
        connector_id="notion",
        credentials={
            "raw_response": {
                "alias": "Marketing workspace",
                "word_id": "notion_blue-otter",
                "account_id": "acc-9",
            }
        },
    )
    assert identity.display_name == "Marketing workspace"
    # provider_account_id derivation is unaffected by the new label fallbacks.
    assert identity.provider_account_id is None


async def test_generic_identity_uses_per_toolkit_subdomain_fields():
    identity = await resolve_account_identity(
        connector_id="zendesk",
        credentials={"raw_response": {"subdomain": "acme-support"}},
    )
    assert identity.display_name == "acme-support"


async def test_generic_identity_email_still_wins_over_word_id():
    identity = await resolve_account_identity(
        connector_id="hubspot",
        credentials={
            "raw_response": {"word_id": "hubspot_green-fox"},
            "email": "sales@acme.test",
        },
    )
    assert identity.display_name == "sales@acme.test"


async def test_generic_identity_falls_back_to_account_id_when_nothing_else():
    identity = await resolve_account_identity(
        connector_id="some-new-toolkit",
        credentials={"raw_response": {"account_id": "acct_123"}},
    )
    assert identity.display_name == "acct_123"


async def test_telegram_identity_calls_getme(monkeypatch):
    class _FakeResp:
        def raise_for_status(self):  # noqa: D401
            return None

        def json(self):
            return {"ok": True, "result": {"id": 123, "username": "lemmabot", "first_name": "Lemma"}}

    class _FakeClient:
        def __init__(self, *a, **k):
            self.posted_url = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url):
            _FakeClient.posted_url = url
            return _FakeResp()

    monkeypatch.setattr(account_identity.httpx, "AsyncClient", _FakeClient)

    identity = await resolve_account_identity(
        connector_id="telegram",
        credentials={"bot_token": "111:AAA", "api_base_url": "http://fake/bot"},
    )
    assert identity.provider_account_id == "123"
    assert identity.display_name == "@lemmabot"
    assert _FakeClient.posted_url == "http://fake/bot111:AAA/getMe"


async def test_telegram_identity_falls_back_to_bot_id_without_username_or_name(monkeypatch):
    class _FakeResp:
        def raise_for_status(self):  # noqa: D401
            return None

        def json(self):
            return {"ok": True, "result": {"id": 456}}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url):
            return _FakeResp()

    monkeypatch.setattr(account_identity.httpx, "AsyncClient", _FakeClient)

    identity = await resolve_account_identity(
        connector_id="telegram",
        credentials={"bot_token": "111:AAA", "api_base_url": "http://fake/bot"},
    )
    assert identity.provider_account_id == "456"
    assert identity.display_name == "456"


async def test_telegram_missing_token_returns_empty():
    identity = await resolve_account_identity(connector_id="telegram", credentials={})
    assert identity.provider_account_id is None
    assert identity.display_name is None


async def test_telegram_getme_failure_is_swallowed(monkeypatch):
    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url):
            raise RuntimeError("network down")

    monkeypatch.setattr(account_identity.httpx, "AsyncClient", _BoomClient)
    identity = await resolve_account_identity(
        connector_id="telegram", credentials={"bot_token": "111:AAA"}
    )
    assert identity == account_identity.AccountIdentity()
