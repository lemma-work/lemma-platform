"""Every client we ship can do the core journey.

We publish a CLI and two SDKs, and each one is a way a real person reaches the
platform. A green API suite says the *server* works; it says nothing about
whether `lemma pods list` maps its arguments correctly, whether the Python SDK
sends the auth header it thinks it does, or whether the TypeScript build is
even shippable.

So the same journey — sign up, make an organization, make a pod, put a table in
it, put a record in the table, read it back — runs through each client. It is
deliberately the *same* journey and not a bespoke one per client: the point is
that they agree, and a difference between two of them is the finding.

These are slower than the API scenarios (a process per call), so this is a
conformance subset rather than full coverage. The API driver stays the workhorse.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.drivers.clients import CliDriver, PythonSdkDriver, TypescriptSdkDriver
from harness.steps.datastore import column

pytestmark = [journey("Clients we ship"), capability("Do the core journey")]


@pytest.fixture
async def signed_in(world):
    """A person with an organization and a pod, plus their bearer token."""
    alice = await world.person("daniel")
    organization = alice.organization
    pod = await alice.works_in("company-wide")
    return alice, organization, pod


@scenario("The CLI lists the pods a person can see")
@proves("PS-POD-030")
@covers("pod.list")
async def test_the_cli_lists_pods(world, signed_in):
    alice, organization, pod = signed_in
    cli = CliDriver(base_url=world.base_url, token=alice.api.token)

    listed = cli.json("pods", "list", org=str(organization["id"]))

    names = {p.get("name") for p in (listed if isinstance(listed, list) else listed.get("items", []))}
    assert pod["name"] in names, (
        f"the CLI could not see a pod the API returns. Saw: {sorted(n for n in names if n)}"
    )


@scenario("The CLI reads a table and its records")
@proves("PS-DATA-011")
@covers("table.list", "record.list")
async def test_the_cli_reads_tables_and_records(world, signed_in):
    alice, organization, pod = signed_in
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title")], shared=True
    )
    await alice.adds_record({"title": "from the api"}, to_table=table["name"], in_pod=pod)
    cli = CliDriver(base_url=world.base_url, token=alice.api.token)

    tables = cli.json("tables", "list", org=str(organization["id"]), pod=str(pod["id"]))
    rows = cli.json(
        "records", "list", table["name"],
        org=str(organization["id"]), pod=str(pod["id"]),
    )

    listed = {t.get("name") for t in (tables if isinstance(tables, list) else tables.get("items", []))}
    assert table["name"] in listed, listed
    titles = {r.get("title") for r in (rows if isinstance(rows, list) else rows.get("items", []))}
    assert "from the api" in titles, (
        f"the CLI could not read a record the API wrote. Saw: {titles}"
    )


@scenario("The Python SDK reads the pods a person can see")
@proves("PS-POD-030")
@covers("pod.list")
async def test_the_python_sdk_lists_pods(world, signed_in):
    alice, organization, pod = signed_in
    sdk = PythonSdkDriver(base_url=world.base_url, token=alice.api.token)

    names = sdk.evaluate(
        f"listed = lemma.pods.list(org_id={str(organization['id'])!r})\n"
        "result = [p.name for p in listed.items]"
    )

    assert pod["name"] in names, (
        f"the Python SDK could not see a pod the API returns. Saw: {names}"
    )


@scenario("The Python SDK writes a record the API can read back")
@proves("PS-DATA-010")
@covers("record.create", "record.list")
async def test_the_python_sdk_writes_a_record(world, signed_in):
    alice, organization, pod = signed_in
    table = await alice.creates_a_table(
        in_pod=pod, columns=[column("title")], shared=True
    )
    sdk = PythonSdkDriver(base_url=world.base_url, token=alice.api.token)

    sdk.evaluate(
        f"pod = lemma.pod({str(pod['id'])!r})\n"
        f"pod.records.create({table['name']!r}, {{'title': 'from the sdk'}})\n"
        "result = 'written'"
    )

    titles = {r["title"] for r in await alice.records_in(table["name"], in_pod=pod)}
    assert "from the sdk" in titles, (
        f"a record the SDK reported writing is not there. Saw: {titles}"
    )


@pytest.mark.xfail(
    reason="DEV-SDK-001: the built dist cannot be imported from Node",
    strict=True,
)
@scenario("The TypeScript SDK reads the pods a person can see")
@proves("PS-POD-030")
@covers("pod.list")
async def test_the_typescript_sdk_lists_pods(world, signed_in):
    alice, organization, pod = signed_in
    sdk = TypescriptSdkDriver(base_url=world.base_url, token=alice.api.token)
    if not sdk.available():
        pytest.skip(
            "lemma-typescript is not built; run `npm ci && npm run build` there"
        )

    names = sdk.evaluate(
        f"const pods = await lemma.pods.list({{ organizationId: {str(organization['id'])!r} }});\n"
        "console.log('<<<RESULT>>>' + JSON.stringify(pods.map((p) => p.name)));"
    )

    assert pod["name"] in names, names
