"""Packaging and reuse → what a bundle must not carry, and must not be able to do.

A bundle is the one artefact of Lemma that leaves Lemma. It gets mailed around,
committed to repositories, and installed by people who did not build it — so
the two questions that matter are what it takes with it on the way out, and what
it can do to you on the way in.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import BUNDLE_QUOTA
from harness.steps.datastore import column

pytestmark = [
    journey("Packaging and reuse"),
    capability("Take a pod somewhere else"),
]

#: Distinctive enough that finding it anywhere in the archive is proof rather
#: than coincidence, and not a real credential of any kind.
SECRET = "sentinel-credential-8f3a1c9e-do-not-export"


@scenario("A bundle carries the work and leaves the secrets behind")
@proves("PS-PACK-002")
@covers("pod.bundle.export.start", "pod.bundle.export.get", "pod.bundle.download")
async def test_an_exported_bundle_contains_no_credentials(world, provider, run):
    needs(BUNDLE_QUOTA)
    alice = await world.person("daniel")
    organization = alice.organization
    auth_config = await alice.installs_http_connector(
        in_organization=organization,
        server_url=provider.base_url,
        spec_url=provider.spec_url,
    )
    await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        credentials={"access_token": SECRET},
    )
    pod = await alice.creates_a_pod(named=run.name("pod"))
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    await alice.adds_record({"title": "real work"}, to_table=table["name"], in_pod=pod)

    export = await alice.exports_pod(pod)
    archive = await alice.downloads_bundle(export)

    # Read every member rather than scanning the compressed bytes: a secret in a
    # deflated entry would not appear in the raw archive, so scanning the
    # container would pass while the bundle still carried it.
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        carrying = [
            name
            for name in bundle.namelist()
            if not name.endswith("/") and SECRET.encode() in bundle.read(name)
        ]

    assert not carrying, (
        f"the bundle carries a connector credential in {carrying} — anyone this "
        f"pod is shared with receives the secret with it"
    )


@scenario("An exported bundle still carries the work it was exported for")
@proves("PS-PACK-002", "PS-OPS-021")
@covers("pod.bundle.export.start", "pod.bundle.download")
async def test_an_exported_bundle_is_readable_without_lemma(world, run):
    needs(BUNDLE_QUOTA)
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    await alice.adds_record(
        {"title": "the work that must survive leaving"},
        to_table=table["name"],
        in_pod=pod,
    )

    export = await alice.exports_pod(pod)
    archive = await alice.downloads_bundle(export)

    # An ordinary zip of text — openable with tools everyone already has. That
    # is the promise: leaving the platform must not mean losing the work, and a
    # format only Lemma can read would fail it while still "exporting".
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = bundle.namelist()
        assert names, "the export produced an empty archive"
        assert bundle.testzip() is None, "the exported archive is corrupt"
        mentions_the_table = any(
            table["name"].encode() in bundle.read(name)
            for name in names
            if not name.endswith("/")
        )

    assert mentions_the_table, (
        f"the export does not mention the table it was exporting; it holds "
        f"{names[:20]}"
    )


@pytest.mark.parametrize(
    ("what", "archive"),
    [
        pytest.param(
            "a path escaping the pod",
            {"../../../../etc/lemma-scenarios-escape": b"owned"},
            id="path-traversal",
        ),
        pytest.param(
            "an absolute path",
            {"/tmp/lemma-scenarios-escape": b"owned"},
            id="absolute-path",
        ),
        pytest.param(
            "nothing a bundle should contain",
            {"manifest.json": b"this is not json at all"},
            id="malformed-manifest",
        ),
    ],
)
@scenario("A hostile bundle is rejected rather than unpacked")
@proves("PS-PACK-013")
@covers("pod.bundle.upload", "pod.bundle.import.start", "pod.bundle.import.get")
async def test_a_hostile_bundle_cannot_reach_outside_the_pod(world, what, archive, run):
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, content in archive.items():
            bundle.writestr(name, content)

    # Either gate is a correct answer — refusing the upload outright, or taking
    # the bytes and failing the plan. What must not happen is a plan that
    # proposes to write where the archive asked it to.
    staged = await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/bundle/uploads",
        files={"data": ("bundle.zip", buffer.getvalue(), "application/zip")},
    )
    if staged.status_code >= 400:
        return

    plan = await alice.plans_import(staged.json()["url"], into_pod=pod)

    assert str(plan.get("status")) == "FAILED", (
        f"a bundle containing {what} produced a usable import plan "
        f"({plan.get('status')}): {str(plan)[:600]}"
    )
