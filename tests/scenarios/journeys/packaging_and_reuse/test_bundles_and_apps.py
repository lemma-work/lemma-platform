"""Packaging and reuse → taking a pod with you, and giving work an interface."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column
from harness.waiting import eventually

pytestmark = [journey("Packaging and reuse"), capability("Take a pod with you")]

TERMINAL_EXPORT = {"READY", "FAILED"}


@pytest.fixture
async def pod(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


@scenario("A person exports a pod and gets something to download")
@proves("PS-PACK-001")
@covers("pod.bundle.export.start", "pod.bundle.export.get", "bundle.exported")
async def test_a_pod_exports_to_a_downloadable_bundle(pod):
    alice, the_pod = pod
    table = await alice.creates_a_table(in_pod=the_pod, columns=[column("title")])
    await alice.adds_record({"title": "packed"}, to_table=table["name"], in_pod=the_pod)

    started = await alice.api.expect(
        "POST",
        f"/pods/{the_pod['id']}/bundle/exports",
        # 202: the export is queued to the worker, not done in the request.
        status=202,
        what=f"{alice.label} starting an export",
        json={},
    )

    finished = await eventually(
        lambda: alice.api.get(
            f"/pods/{the_pod['id']}/bundle/exports/{started['export_id']}"
        ),
        lambda payload: str(payload.get("status")).upper() in TERMINAL_EXPORT,
        describe="the export to finish",
        timeout=90.0,
    )
    assert str(finished["status"]).upper() == "READY", finished
    assert finished.get("download_url") or finished.get("url"), (
        f"a ready export must carry something to download: {finished}"
    )


@scenario("Someone outside the pod cannot export it")
@proves("PS-PACK-001")
@covers("pod.bundle.export.start")
async def test_an_outsider_cannot_export(world, pod):
    alice, the_pod = pod
    outsider = await world.new_person("outsider")

    response = await outsider.api.call(
        "POST", f"/pods/{the_pod['id']}/bundle/exports", json={}
    )

    assert response.status_code >= 400, response.status_code


class TestApps:
    pytestmark = capability("Give the work an interface")

    @scenario("A person creates an app in a pod")
    @proves("PS-PACK-030")
    @covers("app.create", "app.get", "app.list", "app.created")
    async def test_an_app_is_created(self, pod):
        alice, the_pod = pod

        app = await alice.creates_an_app(in_pod=the_pod)

        reopened = await alice.api.get(f"/pods/{the_pod['id']}/apps/{app['name']}")
        assert reopened["name"] == app["name"]
        assert app["name"] in {a["name"] for a in await alice.apps_in(the_pod)}

    @scenario("An app name already used in the pod is refused")
    @proves("PS-PACK-030")
    @covers("app.create")
    async def test_a_duplicate_app_name_is_refused(self, pod):
        alice, the_pod = pod
        app = await alice.creates_an_app(in_pod=the_pod)

        response = await alice.api.call(
            "POST", f"/pods/{the_pod['id']}/apps", json={"name": app["name"]}
        )

        assert response.status_code >= 400, response.status_code

    @scenario("Deleting an app removes it from the pod")
    @proves("PS-PACK-030")
    @covers("app.delete", "app.list")
    async def test_deleting_an_app_removes_it(self, pod):
        alice, the_pod = pod
        app = await alice.creates_an_app(in_pod=the_pod)

        await alice.api.delete(f"/pods/{the_pod['id']}/apps/{app['name']}")

        assert app["name"] not in {a["name"] for a in await alice.apps_in(the_pod)}

    @pytest.mark.xfail(
        reason="DEV-PACK-001: app.get has no pod-membership guard",
        strict=True,
    )
    @scenario("Someone outside the pod cannot read its apps")
    @proves("PS-PACK-031")
    @covers("app.list", "app.get")
    async def test_an_outsider_cannot_read_apps(self, world, pod):
        alice, the_pod = pod
        app = await alice.creates_an_app(in_pod=the_pod)
        outsider = await world.new_person("outsider")

        response = await outsider.api.call(
            "GET", f"/pods/{the_pod['id']}/apps/{app['name']}"
        )

        assert response.status_code >= 400, response.status_code
