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


@scenario("A person publishes an app by shipping a release for it")
@proves("PS-PACK-031")
@covers("app.create", "app.bundle.upload", "app.get")
async def test_shipping_a_release_publishes_the_app(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
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
@covers("app.get", "app.asset.root.get")
async def test_opening_an_app_starts_a_session(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
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
