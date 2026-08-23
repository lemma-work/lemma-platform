"""Packaging and reuse → a pod exported here works when imported there.

The round trip is the whole promise: a bundle is only portable if what comes
out the other end actually runs. These provision a sandbox (imported functions
have their schemas extracted on the way in), so they sit in the `sandbox` lane.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import BUNDLE_QUOTA
from harness.steps.datastore import column

pytestmark = [
    journey("Packaging and reuse"),
    capability("Bring someone else's pod in"),
    pytest.mark.sandbox,
]


@pytest.fixture
async def built_pod(world, run):
    """A pod with a table, records and a function — something worth carrying."""
    needs(BUNDLE_QUOTA)
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("source-pod"))
    table = await alice.creates_a_table(
        in_pod=pod, named="tickets", columns=[column("title"), column("rank", "INTEGER")],
        shared=True,
    )
    await alice.adds_records(
        [{"title": "first", "rank": 1}, {"title": "second", "rank": 2}],
        to_table=table["name"], in_pod=pod,
    )
    await alice.creates_a_function(in_pod=pod, named="bump")
    return alice, pod, table["name"]


@scenario("A pod exported here is importable there, with its resources")
@proves("PS-PACK-001", "PS-PACK-010", "PS-PACK-012")
@covers(
    "pod.bundle.export.start",
    "pod.bundle.download",
    "pod.bundle.upload",
    "pod.bundle.import.start",
    "pod.bundle.import.apply",
    "import.started",
    "import.completed",
)
async def test_a_bundle_round_trips(built_pod, run):
    alice, source, table_name = built_pod

    export = await alice.exports_pod(source)
    assert export["status"] == "READY", export
    archive = await alice.downloads_bundle(export)
    assert archive[:2] == b"PK", "an exported bundle is a zip archive"

    destination = await alice.creates_a_pod(named=run.name("destination-pod"))
    url = await alice.uploads_bundle(archive, into_pod=destination)
    plan = await alice.plans_import(url, into_pod=destination)
    assert plan["status"] == "AWAITING_CONFIRMATION", (
        f"nothing may be applied before a person sees the plan: {plan}"
    )

    applied = await alice.applies_import(plan, into_pod=destination)
    assert applied["status"] == "COMPLETED", applied

    arrived = {t["name"] for t in await alice.tables_in(destination)}
    assert table_name in arrived, arrived
    assert "bump" in {f["name"] for f in await alice.functions_in(destination)}


@scenario("Nothing is applied until the person approves the plan")
@proves("PS-PACK-010")
@covers("pod.bundle.import.start", "table.list")
async def test_the_plan_changes_nothing(built_pod, run):
    alice, source, table_name = built_pod
    export = await alice.exports_pod(source)
    archive = await alice.downloads_bundle(export)
    destination = await alice.creates_a_pod(named=run.name("untouched-pod"))

    url = await alice.uploads_bundle(archive, into_pod=destination)
    await alice.plans_import(url, into_pod=destination)

    assert await alice.tables_in(destination) == [], (
        "planning must not create anything; only applying may"
    )


@scenario("An imported function runs in the pod it landed in")
@proves("PS-PACK-014")
@covers("pod.bundle.import.apply", "function.run")
async def test_an_imported_function_actually_runs(built_pod, run):
    alice, source, _table = built_pod
    export = await alice.exports_pod(source)
    archive = await alice.downloads_bundle(export)
    destination = await alice.creates_a_pod(named=run.name("working-pod"))
    url = await alice.uploads_bundle(archive, into_pod=destination)
    plan = await alice.plans_import(url, into_pod=destination)
    await alice.applies_import(plan, into_pod=destination)

    run = await alice.runs_function("bump", with_input={"value": 1}, in_pod=destination)

    assert run["status"] == "COMPLETED", (
        f"an imported function has to work, not merely exist: {run}"
    )
    assert run["output_data"] == {"value": 2}, run


@scenario("A person cancels an import and nothing is applied")
@proves("PS-PACK-011")
@covers("pod.bundle.import.cancel", "table.list")
async def test_a_cancelled_import_applies_nothing(built_pod, run):
    alice, source, _table = built_pod
    export = await alice.exports_pod(source)
    archive = await alice.downloads_bundle(export)
    destination = await alice.creates_a_pod(named=run.name("cancelled-pod"))
    url = await alice.uploads_bundle(archive, into_pod=destination)
    plan = await alice.plans_import(url, into_pod=destination)

    import_id = plan.get("import_id") or plan.get("id")
    await alice.api.delete(f"/pods/{destination['id']}/bundle/imports/{import_id}")

    assert await alice.tables_in(destination) == [], (
        "a cancelled import must leave the pod exactly as it was"
    )
