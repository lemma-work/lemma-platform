"""Live → Google, as far as a machine can honestly go.

Connecting Google means consenting in a browser. Google deliberately blocks
automated sign-in, so a scenario that drove the consent screen would be fighting
a defence Google maintains on purpose — and would be the flakiest thing in the
suite. This does not pretend otherwise.

What it does instead is test the half that *is* the deployment's responsibility
and the half that actually breaks: the connect flow it hands a person. A rotated
client, a missing scope, a redirect that no longer matches — those are silent
until somebody tries to connect, and they are exactly what this catches.

Using a connected Google account is the tier above, and it needs an environment
where somebody has already consented once. Point the lane at one:

    cd tests/scenarios && uv run pytest -m live --base-url https://your-lemma

See `LIVE.md`.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import COMPOSIO, GOOGLE, needs

pytestmark = [
    journey("Connectors and accounts"),
    capability("Connect an account"),
    pytest.mark.live,
]

#: What a Calendar connection is useless without. A deployment that has dropped
#: these still starts a flow, and the person finds out only when an operation
#: is refused for a scope they were never asked for.
CALENDAR_SCOPES = ("calendar", "userinfo.email")


@scenario("A person is sent to Google with this deployment's own client and scopes")
@proves("PS-CONN-011")
@covers("connector.auth_config.create", "connector.connect_request.create")
async def test_the_google_connect_flow_is_configured(world):
    needs(GOOGLE)
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    auth_config = await alice.installs_connector(
        "google_calendar", in_organization=organization
    )

    started = await alice.starts_connecting(
        in_organization=organization, auth_config=auth_config
    )
    assert started.status_code < 400, (
        f"a configured deployment could not start a Google connect flow: "
        f"{started.status_code} {started.text[:400]}"
    )

    where = started.json().get("redirect_url") or started.json().get("url") or ""
    assert "accounts.google.com" in where, (
        f"the person is not being sent to Google: {where[:200]}"
    )

    query = parse_qs(urlparse(where).query)
    sent_client = (query.get("client_id") or [""])[0]
    assert sent_client == GOOGLE.value("CONNECTOR_GOOGLE_CLIENT_ID"), (
        "the flow uses a different client from the one this deployment is "
        "configured with, so consent would be granted to the wrong application"
    )

    scopes = " ".join(query.get("scope") or [])
    for scope in CALENDAR_SCOPES:
        assert scope in scopes, (
            f"the consent screen would not ask for {scope!r}, so the connection "
            f"succeeds and every operation needing it is then refused. Asked "
            f"for: {scopes}"
        )

    # Without this Google issues no refresh token, and the connection works for
    # an hour and then silently stops.
    assert (query.get("access_type") or [""])[0] == "offline", (
        "the flow does not ask for offline access, so Google returns no refresh "
        "token and the account expires within the hour"
    )


@scenario("A deployment without Composio does not offer its toolkits")
@proves("PS-CONN-001")
@covers("connector.list")
async def test_the_catalogue_matches_what_is_configured(world):
    needs(COMPOSIO)
    alice = await world.new_person("alice")
    await alice.creates_an_organization()

    # `id` is the connector's stable handle — `github`, `gmail`. There is no
    # `name`: reading one gives a set of "None" that satisfies any count.
    catalogue = {str(connector["id"]) for connector in await alice.available_connectors()}

    # Composio's key is set, so its toolkits are in the catalogue alongside the
    # native ones. A catalogue that lists only natives means the import ran
    # without the key and every Composio connector is missing — visible here,
    # and otherwise only when somebody tries to install one.
    assert catalogue, "the connector catalogue is empty"
    assert len(catalogue) > 10, (
        f"only {len(catalogue)} connectors are installable, which looks like "
        f"the Composio half of the catalogue did not import: {sorted(catalogue)}"
    )
