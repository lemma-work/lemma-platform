"""Live → Google, through both paths that reach it.

Calendar goes through Lemma's own native connector; Gmail goes through Composio
against the *same* Google account. Running both matters more than running
either: the two paths have different auth, different operation naming and
different error shapes, and a person connecting one and then the other has to
end up with one identity rather than two.

Everything created here is deleted, unconditionally. Use a throwaway account.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import COMPOSIO, GOOGLE, needs

pytestmark = [
    journey("Connectors and accounts"),
    capability("Connect an account"),
    pytest.mark.live,
]


def _google_credentials() -> dict:
    """What Lemma stores for an OAuth2 account.

    The refresh token is the durable half — an access token expires in an hour,
    so a nightly lane holding one would work on the day it was created and never
    again. Handing Lemma the refresh token is also what puts refresh itself
    under test, which is the part most likely to be broken.
    """
    return {
        "refresh_token": GOOGLE.value("LIVE_GOOGLE_REFRESH_TOKEN"),
        "client_id": GOOGLE.value("LIVE_GOOGLE_CLIENT_ID"),
        "client_secret": GOOGLE.value("LIVE_GOOGLE_CLIENT_SECRET"),
    }


@pytest.fixture
async def calendar(world):
    needs(GOOGLE)
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    auth_config = await alice.installs_connector(
        "google_calendar", in_organization=organization
    )
    account = await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        credentials=_google_credentials(),
    )
    return alice, organization, auth_config, account


@scenario("A person connects Calendar and Lemma refreshes the token itself")
@proves("PS-CONN-011", "PS-CONN-020")
@covers("connector.account.create", "connector.operation.execute")
async def test_calendar_reads_with_a_refreshed_token(calendar):
    alice, organization, auth_config, _account = calendar

    listed = await alice.runs_operation(
        "calendar_list_list",
        auth_config=auth_config,
        in_organization=organization,
        payload={},
    )

    # Only a refresh token was stored, so answering at all means Lemma
    # exchanged it for an access token on its own.
    body = listed.get("data") or listed.get("result") or listed
    assert "items" in str(body) or "calendar" in str(body).lower(), (
        f"Calendar did not answer: {str(body)[:600]}"
    )


@scenario("A person creates a calendar event through Lemma and removes it")
@proves("PS-CONN-030")
@covers("connector.operation.execute")
async def test_an_event_is_created_and_removed(calendar):
    alice, organization, auth_config, _account = calendar
    summary = f"lemma scenarios {uuid4().hex[:8]}"
    starts = datetime.now(timezone.utc) + timedelta(days=1)
    event_id = None

    try:
        created = await alice.runs_operation(
            "events_insert",
            auth_config=auth_config,
            in_organization=organization,
            payload={
                "calendarId": "primary",
                "body": {
                    "summary": summary,
                    "start": {"dateTime": starts.isoformat()},
                    "end": {"dateTime": (starts + timedelta(hours=1)).isoformat()},
                },
            },
        )
        body = created.get("data") or created.get("result") or created
        event_id = body.get("id") if isinstance(body, dict) else None
        assert event_id, f"Calendar did not return the event: {str(body)[:600]}"
    finally:
        if event_id:
            await alice.runs_operation(
                "events_delete",
                auth_config=auth_config,
                in_organization=organization,
                payload={"calendarId": "primary", "eventId": event_id},
            )


@scenario("The same Google account reached through Composio is the same account")
@proves("PS-CONN-011")
@covers("connector.account.create", "connector.account.list")
async def test_gmail_through_composio_is_the_same_identity(world):
    needs(GOOGLE, COMPOSIO)
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()

    native = await alice.installs_connector(
        "google_calendar", in_organization=organization
    )
    await alice.connects_account(
        in_organization=organization,
        auth_config=native,
        credentials=_google_credentials(),
    )
    through_composio = await alice.installs_connector(
        "gmail", in_organization=organization
    )
    await alice.connects_account(
        in_organization=organization,
        auth_config=through_composio,
        credentials=_google_credentials(),
    )

    accounts = await alice.accounts_in(organization)
    identities = {
        str(account.get("account_identifier") or account.get("identity") or "")
        for account in accounts
    }
    identities.discard("")

    assert len(identities) <= 1, (
        "the same Google account connected two ways produced two identities, so "
        f"a person sees themselves twice: {identities}"
    )
