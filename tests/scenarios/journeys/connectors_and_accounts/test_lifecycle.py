"""Connectors and accounts → installing, connecting, using, and removing.

The provider is a server this suite runs (`harness.fake_upstreams`), installed
through the `http` connector kind — which exists so anyone can connect an
internal API described by its own OpenAPI spec. So the operations really
execute, over HTTP, against something that records what it received.

The organization installs; a person connects their own account; a pod declares
the installation as a pod connector and every operation runs through that. The
connector, never the caller, decides whose account a call uses — the caller's
own for a PER_MEMBER connector, the one its owner shared for a SHARED one.
There is no request in which a caller names an account, so there is no
scenario here that tries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.drivers.api import items_of
from harness.provider_view import ProviderView

JSON = dict[str, Any]

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
        # Shaped by the kind's own credential schema: the HTTP connector spends
        # `access_token` as a bearer header, and a catalogue connector such as
        # Telegram requires `bot_token` and refuses anything else.
        credentials=await alice.credential_for(
            auth_config, holding="alice-provider-token"
        ),
    )
    return alice, organization, auth_config, account


async def _a_connector_anyone_can_connect(alice) -> str:
    """A connector from the catalogue this fixture can install unaided.

    Asked of the deployment rather than named here. Which connectors a
    deployment carries is its own business — naming one would make this pass on
    the deployment it was written against and skip on every other.

    Three requirements, and the third is the one that bites. One kind only, so
    installing it without saying which is unambiguous. Not OAuth2, because
    connecting one of those means a person in a browser. And **nothing required
    in its config schema**: installing is a single call with no configuration,
    so a kind that needs a host, a spec URL or a broker key answers 400 and the
    whole file fails on setup.

    That last one is why a preference for a connector that carries operations
    does not belong here. On a real deployment the two are close to disjoint —
    everything with operations wants credentials or an address first, and the
    ones that install unaided are messaging surfaces with none. Ranking on
    operations picked `sql`, which needs `dialect`, `host` and `database`, and
    took eleven scenarios down with it. `test_an_operation_is_readable` asks the
    operations question for itself, where a lane can answer it.
    """
    catalogue = items_of(await alice.api.get("/connectors"))
    connectable = [
        str(connector["id"])
        for connector in catalogue
        if len(connector.get("kinds") or []) == 1
        and str((connector["kinds"][0]).get("auth_scheme", "")).upper() != "OAUTH2"
        and not ((connector["kinds"][0]).get("config_schema") or {}).get("required")
    ]
    for preferred in ("telegram",):
        if preferred in connectable:
            return preferred
    if connectable:
        return connectable[0]
    pytest.skip(
        "this deployment's catalogue has no single-kind connector that can be "
        "installed without configuration and connected without consent, so "
        "there is nothing to install that a scenario could also connect an "
        "account to"
    )


def _a_provider_is_stood_in(stack) -> bool:
    """Is something answering for a third party on this run?

    The same question the `installed` fixture branches on, asked in one place so
    a scenario can tell which kind of connector it ended up with.
    """
    proxy = getattr(stack, "egress", None)
    return proxy is not None and getattr(proxy, "mode", "off") in {"fake", "replay"}


@dataclass(frozen=True, slots=True)
class InAPod:
    """An installation, Priya's account under it, and a pod that declares it."""

    alice: Any
    organization: JSON
    auth_config: JSON
    account: JSON
    pod: JSON
    connector: JSON


@pytest.fixture
async def in_a_pod(installed, run):
    """The installation declared in a fresh pod as a PER_MEMBER connector.

    An operation only ever runs through a pod connector — the pod's own name
    for an installation, which also decides whose account a call uses. So
    every scenario that runs one starts here. PER_MEMBER because it is the
    ordinary case: each caller's own account, picked up on their first call.

    Nothing is added to the pod's accounts here. Priya's account comes in on
    her first call, which is itself one of the things these scenarios watch.
    """
    alice, organization, auth_config, account = installed
    pod = await alice.creates_a_pod(named=run.name("connectors"))
    try:
        connector = await alice.declares_pod_connector(
            auth_config,
            in_pod=pod,
            slug="widgets",
            purpose="Make widgets at the provider",
        )
        yield InAPod(
            alice=alice,
            organization=organization,
            auth_config=auth_config,
            account=account,
            pod=pod,
            connector=connector,
        )
    finally:
        await alice.deletes_pod(pod)


@scenario("An admin installs a connector and its operations are discovered")
# PS-CONN-013 too: this installs the generic `openapi` catalog entry against a
# spec the suite serves, which is exactly how an org connects an API nobody has
# written a connector for. The operations asserted below came from that spec,
# not from the catalog.
@proves("PS-CONN-010", "PS-CONN-013", "PS-CONN-030")
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
    @covers("pod.member.add", "pod.connector.create", "pod.connector.operation.execute")
    async def test_an_account_is_not_shared(self, world, in_a_pod, stack):
        here = in_a_pod
        sofia = await world.person("sofia")
        await here.alice.adds(sofia, to_pod=here.pod, as_role="POD_USER")

        # The same pod connector Priya's own calls run through, asked by a
        # member who has connected nothing. Each member's own account, so for
        # Sofia there is none — and Priya's, one row away, is not a fallback.
        refusal = await sofia.is_refused_running_operation(
            "create_a_widget",
            through=here.connector,
            in_pod=here.pod,
            payload={"body": {"name": "not mine to make"}},
        )

        assert (refusal.status, refusal.code) == (409, "CONNECTION_REQUIRED"), refusal
        assert refusal.details.get("connect_url"), (
            f"a person told to connect must be told where: {refusal}"
        )
        if _a_provider_is_stood_in(stack):
            # The strongest form of "not shared": nothing went out at all, so
            # no credential — Priya's least of all — was presented anywhere.
            reached = ProviderView(stack.egress).calls_to("/widgets")
            assert not reached, (
                "a member with no account of their own reached the provider"
            )

    @scenario("A person disconnects their account and it stops being usable")
    @proves("PS-CONN-020")
    @covers(
        "connector.account.delete",
        "connector.account.list",
        "pod.connector_account.add",
        "pod.connector_account.list",
        "pod.connector.operation.execute",
    )
    async def test_disconnecting_stops_the_account_working(self, in_a_pod):
        here = in_a_pod
        # In the pod first, so disconnecting has something there to take away.
        pod_account = await here.alice.adds_account_to_pod(here.account, in_pod=here.pod)

        await here.alice.deletes_account(here.account, in_organization=here.organization)

        remaining = {
            str(a["id"]) for a in await here.alice.accounts_in(here.organization)
        }
        assert str(here.account["id"]) not in remaining, remaining
        in_the_pod = {str(a["id"]) for a in await here.alice.pod_accounts_in(here.pod)}
        assert str(pod_account["id"]) not in in_the_pod, (
            "a disconnected account is still listed as in use in the pod"
        )
        refusal = await here.alice.is_refused_running_operation(
            "create_a_widget",
            through=here.connector,
            in_pod=here.pod,
            payload={"body": {"name": "after disconnect"}},
        )
        # Not merely refused: refused *because she has no account*, with the
        # way back. A 404 for the operation would pass a ">= 400" and say
        # nothing about the account at all.
        assert (refusal.status, refusal.code) == (409, "CONNECTION_REQUIRED"), refusal
        assert refusal.details.get("connect_url"), refusal

    @scenario("A person reconnects after disconnecting, and it works again")
    @proves("PS-CONN-020", "PS-CONN-022")
    @covers("connector.account.create", "pod.connector.operation.execute")
    async def test_reconnecting_restores_the_account(self, in_a_pod, provider):
        here = in_a_pod
        await here.alice.deletes_account(here.account, in_organization=here.organization)

        await here.alice.connects_account(
            in_organization=here.organization,
            auth_config=here.auth_config,
            credentials=await here.alice.credential_for(
                here.auth_config, holding="alice-new-token"
            ),
        )

        result = await here.alice.runs_operation(
            "create_a_widget",
            through=here.connector,
            in_pod=here.pod,
            payload={"body": {"name": "after reconnect"}},
        )
        assert result is not None
        calls = provider.calls_to("/widgets")
        assert calls, "a reconnected account must reach the provider again"
        assert "alice-new-token" in calls[-1].authorization, (
            f"the call after reconnecting must carry the new credential, not "
            f"the one that was disconnected; got {calls[-1].authorization!r}"
        )


class TestDeclaringAPodConnector:
    pytestmark = capability("Use a connector in a pod")

    @scenario("Only an editor declares which connectors a pod uses")
    @proves("PS-CONN-034")
    @covers("pod.connector.create", "pod.connector.list")
    async def test_a_member_cannot_declare_a_connector(self, world, in_a_pod):
        here = in_a_pod
        sofia = await world.person("sofia")
        await here.alice.adds(sofia, to_pod=here.pod, as_role="POD_USER")

        refusal = await sofia.is_refused_declaring_pod_connector(
            here.auth_config, in_pod=here.pod, slug="not-mine-to-add"
        )

        assert refusal.status == 403, refusal
        declared = {c["slug"] for c in await here.alice.pod_connectors_in(here.pod)}
        assert "not-mine-to-add" not in declared, declared

    @scenario("A shared connector runs as the account its owner shared, for everyone")
    @proves("PS-CONN-020", "PS-CONN-031", "PS-CONN-034")
    @covers(
        "pod.connector_account.add",
        "pod.connector_account.update",
        "pod.connector.create",
        "pod.connector.get",
        "pod.connector.operation.execute",
    )
    async def test_a_shared_connector_runs_as_the_shared_account(
        self, world, in_a_pod, provider
    ):
        here = in_a_pod
        sofia = await world.person("sofia")
        await here.alice.adds(sofia, to_pod=here.pod, as_role="POD_EDITOR")
        pod_account = await here.alice.adds_account_to_pod(here.account, in_pod=here.pod)

        # Sofia can declare connectors. She cannot make Priya's credential
        # serve the pod: only the account's owner shares it, and pointing a
        # shared connector at it before then is exactly that act.
        await sofia.is_refused_sharing_pod_account(pod_account, in_pod=here.pod)
        early = await sofia.is_refused_declaring_pod_connector(
            here.auth_config,
            in_pod=here.pod,
            slug="team-widgets",
            mode="SHARED",
            runs_as=pod_account,
        )
        assert early.status == 400, early
        assert early.details.get("reason") == "account_not_shared", early
        assert not provider.received, "nothing may run before the owner shares"

        await here.alice.shares_pod_account(pod_account, in_pod=here.pod)
        shared = await sofia.declares_pod_connector(
            here.auth_config,
            in_pod=here.pod,
            slug="team-widgets",
            mode="SHARED",
            runs_as=pod_account,
        )
        opened = await sofia.opens_pod_connector(shared, in_pod=here.pod)
        assert str(opened.get("shared_pod_account_id")) == str(pod_account["id"]), opened

        await sofia.runs_operation(
            "create_a_widget",
            through=shared,
            in_pod=here.pod,
            payload={"body": {"name": "made with the team's account"}},
        )

        calls = provider.calls_to("/widgets")
        assert calls, "a shared connector must actually reach the provider"
        assert "alice-provider-token" in calls[-1].authorization, (
            f"a shared connector runs as the account its owner shared, whoever "
            f"calls; got {calls[-1].authorization!r}"
        )

        # And it is still hers to take back. Withdrawn, the connector has no
        # account to run as and says so — rather than falling back to the
        # caller's own, or to nobody's in silence.
        await here.alice.withdraws_pod_account(pod_account, in_pod=here.pod)
        provider.clear()
        withdrawn = await sofia.is_refused_running_operation(
            "create_a_widget",
            through=shared,
            in_pod=here.pod,
            payload={"body": {"name": "after it was withdrawn"}},
        )
        assert (withdrawn.status, withdrawn.code) == (409, "CONNECTOR_NEEDS_ACCOUNT"), (
            withdrawn
        )
        assert not provider.received, "a withdrawn account was still used"


class TestUsingAConnector:
    pytestmark = capability("Do something at the provider")

    @scenario("An operation runs and reaches the provider with the caller's credential")
    @proves("PS-CONN-031")
    @covers(
        "pod.connector.operation.execute",
        "pod.connector_account.list",
        "connector.operation_executed",
    )
    async def test_an_operation_reaches_the_provider(self, in_a_pod, provider):
        here = in_a_pod

        await here.alice.runs_operation(
            "create_a_widget",
            through=here.connector,
            in_pod=here.pod,
            payload={"body": {"name": "made by lemma"}},
        )

        calls = provider.calls_to("/widgets")
        assert calls, "executing an operation must actually call the provider"
        assert calls[0].method == "POST", calls[0]
        assert calls[0].body.get("name") == "made by lemma", calls[0].body
        assert "alice-provider-token" in calls[0].authorization, (
            f"the caller's own credential must be presented; got "
            f"{calls[0].authorization!r}"
        )
        # A per-member call records which of her accounts the pod now uses,
        # so the pod can say whose credentials it holds.
        used = {str(a["account_id"]) for a in await here.alice.pod_accounts_in(here.pod)}
        assert str(here.account["id"]) in used, (
            "the account a pod connector ran as is not recorded in the pod"
        )

    @scenario("A person reads what an operation takes before running it")
    @proves("PS-CONN-030")
    @covers("connector.operation.detail", "connector.operation.search")
    async def test_an_operation_is_readable(self, installed, stack):
        alice, organization, auth_config, _account = installed

        # Whichever operation this connector offers, rather than one named
        # here. The promise is that a person can read what an operation takes
        # before running it — true of any operation, and naming one tied this
        # to the spec the suite serves rather than to the product.
        offered = await alice.operations_of(auth_config, in_organization=organization)
        if not offered and not _a_provider_is_stood_in(stack):
            # Which connector this run could install is the deployment's
            # business, and the ones that install unaided are messaging
            # surfaces that carry no operations — nothing here has failed.
            #
            # Not the skip this used to have, which fired in *either* lane and
            # so stood down at exactly the moment discovery had broken. Where a
            # provider is stood in the spec is served by this suite, operations
            # are guaranteed, and an empty list falls through to the assert.
            pytest.skip(
                "the connector this deployment let the fixture install offers "
                "no operations, so there is nothing whose shape a person could "
                "read. Run against a stack the suite booted to get this one"
            )
        assert offered, (
            "this connector discovered no operations at all, so there is nothing "
            "whose shape a person could read before running it. The installation "
            "succeeded and discovery produced nothing — that is the bug this "
            "scenario is for, not a reason to stand down"
        )
        name = str(offered[0]["name"])

        detail = await alice.operation_detail(
            name, auth_config=auth_config, in_organization=organization
        )

        assert detail.get("name") == name, detail

    @scenario("A pod member sees what a pod connector offers, as its installation does")
    @proves("PS-CONN-030")
    @covers("pod.connector.operation.list", "pod.connector.operation.get")
    async def test_a_pod_connector_offers_its_installations_operations(
        self, in_a_pod, stack
    ):
        here = in_a_pod

        installed = {
            str(o["name"])
            for o in await here.alice.operations_of(
                here.auth_config, in_organization=here.organization
            )
        }
        offered = {
            str(o["name"])
            for o in await here.alice.operations_through(here.connector, in_pod=here.pod)
        }
        if not installed and not _a_provider_is_stood_in(stack):
            pytest.skip(
                "the connector this deployment let the fixture install offers "
                "no operations. Run against a stack the suite booted to get this one"
            )

        # The same set, not "some": a pod connector is a pod's name for an
        # installation, and one that offered less would be a pod quietly
        # unable to do what the organization installed.
        assert offered and offered == installed, (
            f"the pod connector offers {sorted(offered)} where its installation "
            f"offers {sorted(installed)}"
        )
        name = sorted(offered)[0]
        detail = await here.alice.operation_through(
            name, connector=here.connector, in_pod=here.pod
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

    @scenario("Uninstalling a connector says what it stops, then stops it")
    @proves("PS-CONN-012")
    @covers(
        "connector.installation.impact",
        "connector.auth_config.delete",
        "pod.connector.list",
        "pod.connector.operation.execute",
    )
    async def test_uninstalling_stops_everything_under_it(self, in_a_pod):
        here = in_a_pod

        # Before, not after: what removing it would stop, while it can still be
        # reconsidered.
        impact = await here.alice.what_uninstalling_would_stop(
            here.auth_config, in_organization=here.organization
        )
        assert any(
            str(c["pod_id"]) == str(here.pod["id"])
            and c["slug"] == here.connector["slug"]
            for c in impact.get("pod_connectors") or []
        ), f"uninstalling would stop this pod's connector, and did not say so: {impact}"
        assert int(impact.get("account_count") or 0) >= 1, impact

        await here.alice.uninstalls_connector(
            here.auth_config, in_organization=here.organization
        )

        installed_now = {
            c["name"] for c in await here.alice.auth_configs_in(here.organization)
        }
        assert here.auth_config["name"] not in installed_now, installed_now
        declared = {c["slug"] for c in await here.alice.pod_connectors_in(here.pod)}
        assert here.connector["slug"] not in declared, (
            "a pod still lists a connector whose installation is gone"
        )
        refusal = await here.alice.is_refused_running_operation(
            "create_a_widget",
            through=here.connector,
            in_pod=here.pod,
            payload={"body": {"name": "after uninstall"}},
        )
        assert (refusal.status, refusal.code) == (404, "POD_CONNECTOR_NOT_FOUND"), refusal
