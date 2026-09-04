"""Packaging and reuse → an app is published, and its users open it.

PS-PACK-031 names two moments that nothing exercised: the app becoming
published (an app with a release, not a draft), and a person opening it — the
authenticated first call that starts their session. Both are event-bearing
paths; the events themselves are asserted once the suite can capture them.
What a client can see today is what these assert: the app reports itself
published, and opening it works.
"""

from __future__ import annotations

import io
import zipfile

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Packaging and reuse"), capability("Build an app")]


def _dist_bytes() -> bytes:
    """A small but real dist archive: the server versions a dist upload by its
    content hash, so what is inside is not under test — but it must be a zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("index.html", "<html><body>scenario</body></html>")
    return buffer.getvalue()


def _source_bytes() -> bytes:
    """A distinct archive from the dist one, so a mix-up cannot pass."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("src/main.ts", "export const authored = true;\n")
    return buffer.getvalue()


@scenario("A person publishes an app by shipping a release for it")
@proves("PS-PACK-031")
@covers("app.create", "app.bundle.upload", "app.get", "app.published")
async def test_shipping_a_release_publishes_the_app(world, run):
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    app = await alice.creates_an_app(in_pod=pod)

    uploaded = await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/apps/{app['name']}/bundle",
        files={"dist_archive": ("dist.zip", _dist_bytes(), "application/zip")},
    )
    assert uploaded.status_code < 400, uploaded.text[:300]

    shipped = uploaded.json().get("app") or {}
    assert shipped.get("status") == "READY", (
        f"uploading a release did not publish the app: {shipped}"
    )
    assert shipped.get("current_release_id"), shipped

    reopened = await alice.api.get(f"/pods/{pod['id']}/apps/{app['name']}")
    assert reopened.get("status") == "READY", reopened


@scenario("Opening an app marks the session as theirs")
@proves("PS-PACK-031")
@covers("app.get", "app.asset.root.get", "app.session_started")
async def test_opening_an_app_starts_a_session(world, run):
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    app = await alice.creates_an_app(in_pod=pod)

    uploaded = await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/apps/{app['name']}/bundle",
        files={"dist_archive": ("dist.zip", _dist_bytes(), "application/zip")},
    )
    assert uploaded.status_code < 400, uploaded.text[:300]
    app_id = (uploaded.json().get("app") or {}).get("id")

    # The app announces itself on every call it makes: these two headers are
    # what make a request the app's rather than the person's own browsing, and
    # they are what turns it into a counted session.
    opened = await alice.api.call(
        "GET",
        f"/pods/{pod['id']}/apps/{app['name']}",
        headers={
            "X-Lemma-Client": "lemma-app/1.0.0",
            "X-Lemma-App": str(app_id),
        },
    )
    assert opened.status_code == 200, opened.text[:300]


@scenario("An app's source comes back exactly as it was uploaded")
@proves("PS-PACK-032")
@covers("app.bundle.upload", "app.source.archive.get")
async def test_an_apps_source_is_returned_byte_for_byte(world, run):
    """The half of PS-PACK-032 that was not being checked.

    Only the empty case was covered — an app with no release 404s — so the
    suite proved what happens when there is nothing to hand back and never
    that handing it back works. This is a person's escape hatch: what they get
    must be what they authored, not the built output and not a re-zip, or
    "you can always get your source out" is not a promise anybody can rely on.
    """
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    app = await alice.creates_an_app(in_pod=pod)
    source = _source_bytes()

    uploaded = await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/apps/{app['name']}/bundle",
        files={
            "dist_archive": ("dist.zip", _dist_bytes(), "application/zip"),
            "source_archive": ("source.zip", source, "application/zip"),
        },
    )
    assert uploaded.status_code < 400, uploaded.text[:300]

    fetched = await alice.app_source_archive(app["name"], in_pod=pod)

    assert fetched.status_code == 200, (
        f"the source of a released app answered {fetched.status_code}: "
        f"{fetched.text[:200]}"
    )
    assert fetched.content == source, (
        "the archive that came back is not the archive that went in "
        f"({len(fetched.content)} bytes vs {len(source)})"
    )


@scenario("Someone outside the pod cannot download an app's source")
@proves("PS-PACK-032")
@covers("app.source.archive.get")
async def test_an_outsider_cannot_take_the_source(world, run):
    """Source is the whole of the work; reading it is not a public act."""
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    app = await alice.creates_an_app(in_pod=pod)
    await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/apps/{app['name']}/bundle",
        files={
            "dist_archive": ("dist.zip", _dist_bytes(), "application/zip"),
            "source_archive": ("source.zip", _source_bytes(), "application/zip"),
        },
    )

    outsider = await world.person("hannah")
    refused = await outsider.app_source_archive(app["name"], in_pod=pod)

    assert refused.status_code in {403, 404}, (
        f"somebody from another organization downloaded an app's source "
        f"({refused.status_code})"
    )
