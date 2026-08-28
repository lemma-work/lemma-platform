"""Connectors and accounts → installing, connecting, using, and removing.

The provider is a server this suite runs (`harness.fake_upstreams`), installed
through the `http` connector kind — which exists so anyone can connect an
internal API described by its own OpenAPI spec. So the operations really
execute, over HTTP, against something that records what it received.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.drivers.api import items_of

pytestmark = [
    journey("Connectors and accounts"),
    capability("Install a connector for the organization"),
]


@pytest.fixture
async def installed(world, stack):
    """An organization with a connector installed and an account connected.

    Takes `stack` rather than `provider`, and that is the whole point. Most of
    what this file proves is about the *installation* and the *account* —
    scoping, renaming, disconnecting, listing what a connector offers, and what
    uninstalling takes with it. None of that needs a third party to answer;
    only the four scenarios that actually run an operation do, and those ask
    for `provider` by name as well.

    Asking for it here anyway meant all twelve skipped against a deployment,
    where nothing stands in for anything. The eight that never touch it now run
    there.

    Where there is no proxy, the connector comes from the deployment's own
    catalogue instead of from a spec the suite serves. It has to be a
    credential-managed kind: an OAuth2 one cannot be connected without somebody
    opening a browser, which is exactly the step a scenario cannot take.
    """
    alice = await world.person("priya")
    organization = alice.organization
    # Not merely "is there an Egress object": a run with SCENARIOS_EGRESS=off
    # against a stack it booted still has one, in mode `off`, serving nothing.
    # Asking whether anything is actually standing in is the question that
    # matters, and getting it wrong sends the fixture to install an HTTP
    # connector from a spec URL nobody answers — a 500 from the deployment,
    # which reads as the product being broken.
    proxy = getattr(stack, "egress", None)
    if proxy is not None and getattr(proxy, "mode", "off") in {"fake", "replay"}:
        from harness.provider_view import ProviderView

        proxy.forget()
        served = ProviderView(proxy)
        auth_config = await alice.installs_http_connector(
            in_organization=organization,
            server_url=served.base_url,
            spec_url=served.spec_url,
        )
    else:
        auth_config = await alice.installs_connector(
            await _a_connector_anyone_can_connect(alice),
            in_organization=organization,
        )
    account = await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        # `access_token` is the key the HTTP executor turns into a bearer
        # header. A credential under any other name is stored and never sent —
        # which is why the same one works for a catalogue connector that has
        # never heard of it: a credential-managed account stores what it is
        # given, and nothing here asks it to be spent.
        credentials={"access_token": "alice-provider-token"},
    )
    return alice, organization, auth_config, account


async def _a_connector_anyone_can_connect(alice) -> str:
    """A connector from the catalogue that needs no consent to connect.

    Asked of the deployment rather than named here. Which connectors a
    deployment carries is its own business — naming one would make this pass on
    the deployment it was written against and skip on every other.

    One kind only, so installing it without saying which is unambiguous; not
    OAuth2, because connecting one of those means a person in a browser; and
    `telegram` first where the catalogue has it.

    That preference is not cosmetic. Installing a connector *discovers* what it
    offers, and for a brokered one that means a live call to the broker —
    which turns a fixture into a dependency on somebody else's uptime, and
    fails outright where that broker cannot be reached. Telegram's operations
    are built in, so installing it asks nobody anything. The suite already
    depends on it standing (`tenant.STANDING_CONNECTORS`), so preferring it
    here assumes nothing new.
    """
    catalogue = items_of(await alice.api.get("/connectors"))
    connectable = [
        str(connector["id"])
        for connector in catalogue
        if len(connector.get("kinds") or []) == 1
        and str((connector["kinds"][0]).get("auth_scheme", "")).upper() != "OAUTH2"
    ]
    for preferred in ("telegram",):
        if preferred in connectable:
            return preferred
    if connectable:
        return connectable[0]
    pytest.skip(
        "this deployment's catalogue has no single-kind connector that can be "
        "connected without consent, so there is nothing to install that a "
        "scenario could also connect an account to"
    )


@scenario("An admin installs a connector and its operations are discovered")
@proves("PS-CONN-010", "PS-CONN-030")
@covers(
    "connector.auth_config.create",
    "connector.auth_config.list",
    "connector.auth_config.get",
    "connector.operation.discover",
)
async def test_installing_discovers_operations(world, provider):
    alice = await world.person("priya")
    organization = alice.organization

    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url,
        spec_url=provider.spec_url,
    )

    installed = {c["name"] for c in await alice.auth_configs_in(organization)}
    assert auth_config["name"] in installed, installed

    operations = {
        o["name"]
        for o in await alice.operations_of(auth_config, in_organization=organization)
    }
    assert operations, "installing must discover what the provider offers"
    assert "create_a_widget" in operations, operations


@scenario("An installation is scoped to its organization")
@proves("PS-CONN-010")
@covers("connector.auth_config.list", "connector.auth_config.get")
async def test_an_installation_does_not_leak_across_organizations(world, installed):
    _alice, _organization, auth_config, _account = installed
    # Calder Retail, a company that genuinely exists on this tenant, rather than
    # a second organization made to prove a point and left behind. Hannah looks
    # for it herself: Priya cannot read another company's connectors at all,
    # which is the right answer and the wrong question to ask here.
    hannah = await world.person("hannah")

    theirs = {
        str(config["id"]) for config in await hannah.auth_configs_in(hannah.organization)
    }

    assert str(auth_config["id"]) not in theirs, (
        "installing a connector in one organization put it in another"
    )


@scenario("Someone outside the organization cannot install or see connectors")
@proves("PS-CONN-010")
@covers("connector.auth_config.create", "connector.auth_config.list")
async def test_an_outsider_cannot_install(world, installed, provider):
    alice, organization, _auth_config, _account = installed
    outsider = await world.person("hannah")

    listed = await outsider.api.call(
        "GET", f"/organizations/{organization['id']}/connectors/auth-configs"
    )
    created = await outsider.api.call(
        "POST",
        f"/organizations/{organization['id']}/connectors/auth-configs",
        json={
            "connector_id": "openapi",
            "kind": "http",
            "name": "trespass",
            "config": {"server_url": provider.base_url, "spec_url": provider.spec_url},
        },
    )

    assert listed.status_code >= 400, listed.status_code
    assert created.status_code >= 400, created.status_code


@scenario("An admin renames an installation and it keeps working")
@proves("PS-CONN-010")
@covers("connector.auth_config.update", "connector.operation.discover")
async def test_an_installation_can_be_renamed(installed):
    alice, organization, auth_config, _account = installed

    before = {
        str(operation["name"])
        for operation in await alice.operations_of(
            auth_config, in_organization=organization
        )
    }

    new_name = f"{auth_config['name']}_renamed"
    await alice.renames_auth_config(
        auth_config, to=new_name, in_organization=organization
    )

    installed_now = {c["name"] for c in await alice.auth_configs_in(organization)}
    assert new_name in installed_now, installed_now
    after = {
        str(operation["name"])
        for operation in await alice.operations_of(
            {"name": new_name}, in_organization=organization
        )
    }
    # Against what it offered before, not merely "some". The promise is that a
    # rename does not disturb the installation, and "still has at least one
    # operation" is true of an installation that quietly lost half of them. It
    # also stops the assertion depending on which connector this run installed.
    assert after == before, (
        f"a rename lost discovered operations: {sorted(before - after)} went missing"
    )


class TestConnectingAnAccount:
    pytestmark = capability("Connect your own account")

    @scenario("A person connects an account and it belongs to them")
    @proves("PS-CONN-020")
    @covers(
        "connector.account.create",
        "connector.account.list",
        "connector.account.get",
        "connector.connected",
    )
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
        bob = await world.person("sofia")

        await bob.is_refused_running_operation(
            "create_a_widget",
            auth_config=auth_config,
            in_organization=organization,
            payload={"body": {"name": "not mine to make"}},
            account=account,
        )

    @scenario("A person disconnects their account and it stops being usable")
    @proves("PS-CONN-020")
    @covers(
        "connector.account.delete",
        "connector.account.list",
        "connector.operation.execute",
    )
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

        # Whichever operation this connector offers, rather than one named
        # here. The promise is that a person can read what an operation takes
        # before running it — true of any operation, and naming one tied this
        # to the spec the suite serves rather than to the product.
        offered = await alice.operations_of(auth_config, in_organization=organization)
        if not offered:
            pytest.skip(
                "this connector discovered no operations, so there is nothing "
                "to read the shape of"
            )
        name = str(offered[0]["name"])

        detail = await alice.operation_detail(
            name, auth_config=auth_config, in_organization=organization
        )

        assert detail.get("name") == name, detail

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
