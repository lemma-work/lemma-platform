"""Connectors and accounts → installing, connecting, using, and removing.

The provider is a server this suite runs (`harness.fake_platform`), installed
through the `http` connector kind — which exists so anyone can connect an
internal API described by its own OpenAPI spec. So the operations really
execute, over HTTP, against something that records what it received.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.fake_platform import start_fake_provider

pytestmark = [
    journey("Connectors and accounts"),
    capability("Install a connector for the organization"),
]


@pytest.fixture
async def provider():
    fake = start_fake_provider()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture
async def installed(world, provider):
    """An organization with the provider installed and an account connected."""
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url,
        spec_url=provider.spec_url,
    )
    account = await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        # `access_token` is the key the HTTP executor turns into a bearer
        # header. A credential under any other name is stored and never sent.
        credentials={"access_token": "alice-provider-token"},
    )
    provider.clear()
    return alice, organization, auth_config, account


@scenario("An admin installs a connector and its operations are discovered")
@proves("PS-CONN-010", "PS-CONN-030")
@covers(
    "connector.auth_config.create",
    "connector.auth_config.list",
    "connector.auth_config.get",
    "connector.operation.discover",
)
async def test_installing_discovers_operations(world, provider):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()

    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url,
        spec_url=provider.spec_url,
    )

    installed = {c["name"] for c in await alice.auth_configs_in(organization)}
    assert auth_config["name"] in installed, installed

    operations = {o["name"] for o in await alice.operations_of(
        auth_config, in_organization=organization
    )}
    assert operations, "installing must discover what the provider offers"
    assert "create_a_widget" in operations, operations


@scenario("An installation is scoped to its organization")
@proves("PS-CONN-010")
@covers("connector.auth_config.list", "connector.auth_config.get")
async def test_an_installation_does_not_leak_across_organizations(world, installed):
    alice, organization, auth_config, _account = installed
    elsewhere = await alice.creates_an_organization()

    assert await alice.auth_configs_in(elsewhere) == [], (
        "installing in one organization must have no effect in another"
    )


@scenario("Someone outside the organization cannot install or see connectors")
@proves("PS-CONN-010")
@covers("connector.auth_config.create", "connector.auth_config.list")
async def test_an_outsider_cannot_install(world, installed, provider):
    alice, organization, _auth_config, _account = installed
    outsider = await world.new_person("outsider")

    listed = await outsider.api.call(
        "GET", f"/organizations/{organization['id']}/connectors/auth-configs"
    )
    created = await outsider.api.call(
        "POST", f"/organizations/{organization['id']}/connectors/auth-configs",
        json={"connector_id": "openapi", "kind": "http", "name": "trespass",
              "config": {"server_url": provider.base_url, "spec_url": provider.spec_url}},
    )

    assert listed.status_code >= 400, listed.status_code
    assert created.status_code >= 400, created.status_code


@scenario("An admin renames an installation and it keeps working")
@proves("PS-CONN-010")
@covers("connector.auth_config.update", "connector.operation.discover")
async def test_an_installation_can_be_renamed(installed, provider):
    alice, organization, auth_config, _account = installed

    new_name = f"{auth_config['name']}_renamed"
    await alice.renames_auth_config(
        auth_config, to=new_name, in_organization=organization
    )

    installed_now = {c["name"] for c in await alice.auth_configs_in(organization)}
    assert new_name in installed_now, installed_now
    operations = await alice.operations_of(
        {"name": new_name}, in_organization=organization
    )
    assert operations, "a rename must not lose the discovered operations"


class TestConnectingAnAccount:
    pytestmark = capability("Connect your own account")

    @scenario("A person connects an account and it belongs to them")
    @proves("PS-CONN-020")
    @covers("connector.account.create", "connector.account.list",
            "connector.account.get", "connector.connected")
    async def test_an_account_belongs_to_who_connected_it(self, installed):
        alice, organization, _auth_config, account = installed

        mine = await alice.accounts_in(organization)

        assert any(str(a["id"]) == str(account["id"]) for a in mine), mine
        assert str(account["user_id"]) == str(alice.user_id), (
            "an account is owned by the person who connected it, not the org"
        )

    @scenario("One person cannot use another person's connected account")
    @proves("PS-CONN-020", "PS-CONN-031")
    @covers("connector.operation.execute", "connector.account.get")
    async def test_an_account_is_not_shared(self, world, installed):
        alice, organization, auth_config, account = installed
        bob = await world.new_person("bob")
        await bob.accepts(await alice.invites(bob, to=organization))

        await bob.is_refused_running_operation(
            "create_a_widget",
            auth_config=auth_config,
            in_organization=organization,
            payload={"body": {"name": "not mine to make"}},
            account=account,
        )

    @scenario("A person disconnects their account and it stops being usable")
    @proves("PS-CONN-020")
    @covers("connector.account.delete", "connector.account.list", "connector.operation.execute")
    async def test_disconnecting_stops_the_account_working(self, installed):
        alice, organization, auth_config, account = installed

        await alice.deletes_account(account, in_organization=organization)

        remaining = {str(a["id"]) for a in await alice.accounts_in(organization)}
        assert str(account["id"]) not in remaining, remaining
        await alice.is_refused_running_operation(
            "create_a_widget",
            auth_config=auth_config,
            in_organization=organization,
            payload={"body": {"name": "after disconnect"}},
            account=account,
        )

    @scenario("A person reconnects after disconnecting, and it works again")
    @proves("PS-CONN-020", "PS-CONN-022")
    @covers("connector.account.create", "connector.operation.execute")
    async def test_reconnecting_restores_the_account(self, installed, provider):
        alice, organization, auth_config, account = installed
        await alice.deletes_account(account, in_organization=organization)

        reconnected = await alice.connects_account(
            in_organization=organization,
            auth_config=auth_config,
            credentials={"access_token": "alice-new-token"},
        )

        result = await alice.runs_operation(
            "create_a_widget",
            auth_config=auth_config,
            in_organization=organization,
            payload={"body": {"name": "after reconnect"}},
            account=reconnected,
        )
        assert result is not None
        assert provider.calls_to("/widgets"), (
            "a reconnected account must reach the provider again"
        )


class TestUsingAConnector:
    pytestmark = capability("Do something at the provider")

    @scenario("An operation runs and reaches the provider with the caller's credential")
    @proves("PS-CONN-031")
    @covers("connector.operation.execute", "connector.operation_executed")
    async def test_an_operation_reaches_the_provider(self, installed, provider):
        alice, organization, auth_config, account = installed

        await alice.runs_operation(
            "create_a_widget",
            auth_config=auth_config,
            in_organization=organization,
            payload={"body": {"name": "made by lemma"}},
            account=account,
        )

        calls = provider.calls_to("/widgets")
        assert calls, "executing an operation must actually call the provider"
        assert calls[0].method == "POST", calls[0]
        assert calls[0].body.get("name") == "made by lemma", calls[0].body
        assert "alice-provider-token" in calls[0].authorization, (
            f"the caller's own credential must be presented; got "
            f"{calls[0].authorization!r}"
        )

    @scenario("A person reads what an operation takes before running it")
    @proves("PS-CONN-030")
    @covers("connector.operation.detail", "connector.operation.search")
    async def test_an_operation_is_readable(self, installed):
        alice, organization, auth_config, _account = installed

        detail = await alice.operation_detail(
            "create_a_widget", auth_config=auth_config, in_organization=organization
        )

        assert detail.get("name") == "create_a_widget", detail

    @scenario("A person sees what a connector can notify them about")
    @proves("PS-CONN-040")
    @covers("connector.trigger.list")
    async def test_triggers_are_listable(self, installed):
        alice, organization, auth_config, _account = installed

        triggers = await alice.triggers_of(auth_config, in_organization=organization)

        assert isinstance(triggers, list), triggers


class TestRemovingAnInstallation:
    pytestmark = capability("Remove an installation")

    @scenario("Uninstalling a connector stops its accounts being usable")
    @proves("PS-CONN-012")
    @covers("connector.auth_config.delete", "connector.operation.execute")
    async def test_uninstalling_stops_everything_under_it(self, installed):
        alice, organization, auth_config, account = installed

        await alice.uninstalls_connector(auth_config, in_organization=organization)

        installed_now = {c["name"] for c in await alice.auth_configs_in(organization)}
        assert auth_config["name"] not in installed_now, installed_now
        await alice.is_refused_running_operation(
            "create_a_widget",
            auth_config=auth_config,
            in_organization=organization,
            payload={"body": {"name": "after uninstall"}},
            account=account,
        )
