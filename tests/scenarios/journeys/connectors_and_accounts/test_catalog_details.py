"""Connectors and accounts → reading the catalogue and refreshing it."""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Connectors and accounts"),
    capability("Find what can be connected"),
]


@scenario("A person opens a connector and reads what it can do")
@proves("PS-CONN-001")
@covers("connector.get", "connector.list", "connector.skill.get")
async def test_a_connector_reads_back(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    listed = await alice.available_connectors()
    assert listed, "the catalogue should not be empty once seeded"
    connector_id = listed[0].get("id") or listed[0].get("connector_id")

    opened = await alice.opens_connector(connector_id)
    skill = await alice.skill_for(connector_id)

    assert opened is not None, opened
    assert skill.status_code < 500, skill.text[:300]


@scenario("An admin refreshes what an installed connector offers")
@proves("PS-CONN-030")
@covers("connector.auth_config.refresh_operations", "connector.operation.discover")
async def test_operations_can_be_refreshed(world, provider):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url, spec_url=provider.spec_url,
    )

    response = await alice.refreshes_operations(
        auth_config, in_organization=organization
    )

    assert response.status_code < 400, response.text[:300]
    assert await alice.operations_of(auth_config, in_organization=organization)


@scenario("A person reads several operations at once")
@proves("PS-CONN-030")
@covers("connector.operation.details.batch")
async def test_operations_read_in_bulk(world, provider):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url, spec_url=provider.spec_url,
    )

    response = await alice.operation_details(
        ["create_a_widget", "list_widgets"],
        auth_config=auth_config, in_organization=organization,
    )

    assert response.status_code < 400, response.text[:300]


@scenario("A person reads one trigger in full")
@proves("PS-CONN-040")
@covers("connector.trigger.get", "connector.trigger.list")
async def test_a_trigger_reads_back(world, provider):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url, spec_url=provider.spec_url,
    )

    triggers = await alice.triggers_of(auth_config, in_organization=organization)

    if not triggers:
        # An OpenAPI provider declares no triggers, so reading one must say so
        # rather than inventing an answer.
        response = await alice.api.call(
            "GET",
            f"/organizations/{organization['id']}/connectors/"
            f"{auth_config['name']}/triggers/nothing_here",
        )
        assert response.status_code == 404, response.status_code
    else:
        detail = await alice.api.get(
            f"/organizations/{organization['id']}/connectors/"
            f"{auth_config['name']}/triggers/{triggers[0]['name']}"
        )
        assert detail is not None


@scenario("Installing an OAuth connector with no credentials says what is missing")
@proves("PS-CONN-010", "PS-CONN-011")
@covers("connector.auth_config.create")
async def test_an_oauth_connector_needs_credentials(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()

    response = await alice.api.call(
        "POST", f"/organizations/{organization['id']}/connectors/auth-configs",
        json={"connector_id": "slack", "name": "slack_no_creds"},
    )

    assert response.status_code >= 400, response.status_code
    body = response.text.lower()
    assert "oauth" in body and "credential" in body, (
        f"the refusal should name what is missing and what to do instead: "
        f"{response.text[:300]}"
    )


@scenario("Starting a connection for a connector that needs no consent is refused")
@proves("PS-CONN-021")
@covers("connector.connect_request.create")
async def test_connecting_needs_a_consent_flow(world, provider):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    # An API connected by its own credential has no consent screen to send
    # anyone to, so asking to start one is a request that cannot be honoured.
    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url, spec_url=provider.spec_url,
    )

    response = await alice.starts_connecting(
        in_organization=organization, auth_config=auth_config
    )

    assert response.status_code >= 400, (
        f"there is no consent screen for this kind of connector, so starting "
        f"one should be refused rather than half-succeed ({response.status_code})"
    )


@scenario("A connection callback with unrecognised state is refused")
@proves("PS-CONN-021")
@covers("connector.oauth.callback")
async def test_an_unknown_callback_is_refused(world):
    anonymous = await world.new_person("anonymous", sign_up=False)

    response = await anonymous.api.call(
        "GET", "/connectors/connect-requests/oauth/callback",
        params={"state": "not-a-state-we-issued", "code": "whatever"},
    )

    assert response.status_code >= 400 or "error" in response.text.lower(), (
        f"a callback we never started must not be honoured: "
        f"{response.status_code} {response.text[:200]}"
    )
