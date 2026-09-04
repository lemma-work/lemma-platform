"""Packaging and reuse → handing somebody a bundle they are not a member for.

A bundle is shared by link, which means the person who opens it is by
definition somebody without access to the pod that made it. That is the point,
and it is also the risk: the link has to work for a stranger and stop working
when it should, and the two are the same mechanism.
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


@pytest.fixture
async def a_shared_bundle(world, run):
    """A pod exported, and the link its owner would send somebody."""
    needs(BUNDLE_QUOTA)
    alice = await world.person("daniel")
    pod = await alice.creates_a_pod(named=run.name("pod"))
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    await alice.adds_record(
        {"title": "worth sharing"}, to_table=table["name"], in_pod=pod
    )
    export = await alice.exports_pod(pod)
    link = export.get("download_url") or export.get("url")
    assert link, f"the export carries no link to share: {export}"
    return alice, pod, table, link


@scenario("Somebody outside the pod can see what a shared bundle holds")
@proves("PS-PACK-021")
@covers("pod.bundle.download", "share_link.viewed")
async def test_a_stranger_can_read_a_shared_bundle(world, a_shared_bundle):
    alice, pod, table, link = a_shared_bundle
    del alice, pod

    # Nobody: a signed-in person with no connection to the pod at all.
    stranger = await world.person("hannah")
    fetched = await stranger.api.call("GET", link)

    assert fetched.status_code == 200, (
        f"a shared link did not work for the person it was shared with "
        f"({fetched.status_code}), which is the only thing sharing is for: "
        f"{fetched.text[:300]}"
    )
    with zipfile.ZipFile(io.BytesIO(fetched.content)) as bundle:
        contents = bundle.namelist()
    assert any(
        table["name"].encode() in bundle_bytes
        for bundle_bytes in (
            zipfile.ZipFile(io.BytesIO(fetched.content)).read(name)
            for name in contents
            if not name.endswith("/")
        )
    ), f"the bundle does not describe what it contains: {contents[:20]}"


@scenario("Reading a shared bundle does not let a stranger into the pod")
@proves("PS-PACK-021", "PS-ACCESS-001")
@covers("pod.bundle.download", "pod.get")
async def test_reading_a_bundle_grants_nothing_else(world, a_shared_bundle):
    alice, pod, table, link = a_shared_bundle
    del alice, table

    stranger = await world.person("hannah")
    await stranger.api.call("GET", link)

    # Seeing a bundle is seeing a snapshot somebody chose to send. It must not
    # become a way into the pod it came from.
    await stranger.is_refused_pod(pod)


@scenario("A share link that is not genuine is refused")
@proves("PS-PACK-021")
@covers("pod.bundle.download")
async def test_a_forged_link_is_refused(world, a_shared_bundle):
    alice, pod, table, link = a_shared_bundle
    del alice, pod, table

    tampered = link[:-4] + ("aaaa" if not link.endswith("aaaa") else "bbbb")
    refused = await (await world.person("hannah")).api.call("GET", tampered)

    assert refused.status_code >= 400, (
        f"a tampered download token was honoured ({refused.status_code}), which "
        f"makes the signature decorative"
    )
