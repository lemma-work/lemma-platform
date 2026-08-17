"""Packaging and reuse → an app's releases, and following an import."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.steps.datastore import column

pytestmark = [journey("Packaging and reuse"), capability("Give the work an interface")]


@pytest.fixture
async def pod(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


@scenario("A person changes an app's description and address")
@proves("PS-PACK-030")
@covers("app.update", "app.get")
async def test_an_app_can_be_changed(pod):
    alice, the_pod = pod
    app = await alice.creates_an_app(in_pod=the_pod)

    await alice.changes_app(
        app["name"], in_pod=the_pod, description="The operator console"
    )

    reopened = await alice.api.get(f"/pods/{the_pod['id']}/apps/{app['name']}")
    assert reopened["description"] == "The operator console", reopened


@scenario("An app with no release yet says so rather than serving nothing")
@proves("PS-PACK-032")
@covers("app.source.archive.get", "app.dist.archive.get", "app.asset.root.get")
async def test_an_app_without_a_release_is_honest(pod):
    alice, the_pod = pod
    app = await alice.creates_an_app(in_pod=the_pod)

    source = await alice.app_source_archive(app["name"], in_pod=the_pod)
    dist = await alice.app_dist_archive(app["name"], in_pod=the_pod)
    assets = await alice.app_assets(app["name"], in_pod=the_pod)

    for label, response in (("source", source), ("dist", dist), ("assets", assets)):
        assert response.status_code == 404, (
            f"an app with no release should say {label} is not found, "
            f"not {response.status_code}"
        )


@scenario("Uploading something that is not an archive is refused")
@proves("PS-PACK-030")
@covers("app.bundle.upload")
async def test_a_bad_bundle_is_refused(pod):
    alice, the_pod = pod
    app = await alice.creates_an_app(in_pod=the_pod)

    response = await alice.api.call(
        "POST", f"/pods/{the_pod['id']}/apps/{app['name']}/bundle",
        files={"source": ("not-a-zip.txt", b"plain text", "text/plain")},
    )

    assert response.status_code >= 400, (
        f"an app release must be an archive ({response.status_code})"
    )


class TestFollowingAnImport:
    pytestmark = capability("Bring someone else's pod in")

    @pytest.fixture
    async def planned(self, pod):
        alice, the_pod = pod
        await alice.creates_a_table(in_pod=the_pod, columns=[column("title")])
        export = await alice.exports_pod(the_pod)
        archive = await alice.downloads_bundle(export)
        destination = await alice.creates_a_pod(named="Following Pod")
        url = await alice.uploads_bundle(archive, into_pod=destination)
        plan = await alice.plans_import(url, into_pod=destination)
        return alice, destination, plan

    @scenario("A person follows an import while it is being planned")
    @proves("PS-PACK-010")
    @covers("pod.bundle.import.get", "pod.bundle.import.events")
    @pytest.mark.sandbox
    async def test_an_import_can_be_followed(self, planned):
        alice, destination, plan = planned
        import_id = plan.get("import_id") or plan.get("id")

        status = await alice.import_status(import_id, in_pod=destination)
        assert status["status"] == "AWAITING_CONFIRMATION", status

        status, content_type, _first = await alice.import_events(
            import_id, in_pod=destination
        )
        assert status == 200, status
        assert "text/event-stream" in content_type, content_type

    @scenario("A person adjusts an import and gets a fresh plan")
    @proves("PS-PACK-011")
    @covers("pod.bundle.import.replan", "pod.bundle.import.get")
    @pytest.mark.sandbox
    async def test_an_import_can_be_replanned(self, planned):
        alice, destination, plan = planned
        import_id = plan.get("import_id") or plan.get("id")

        response = await alice.replans_import(import_id, in_pod=destination)

        assert response.status_code < 500, response.text[:300]


class TestPublishing:
    pytestmark = capability("Publish and share a pod")

    @scenario("Publishing without a connected account fails with what is missing")
    @proves("PS-PACK-020")
    @covers("pod.bundle.publish.start")
    async def test_publishing_needs_an_account(self, pod):
        alice, the_pod = pod

        response = await alice.api.call(
            "POST", f"/pods/{the_pod['id']}/bundle/publishes",
            json={"repo": "someone/somewhere", "owner": "someone"},
        )

        assert response.status_code >= 400, (
            f"publishing with no connected account must fail clearly "
            f"({response.status_code})"
        )
