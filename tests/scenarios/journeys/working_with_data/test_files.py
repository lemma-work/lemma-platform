"""Working with data → putting documents in, and getting them back out."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Working with data"), capability("Put documents in")]


@pytest.fixture
async def pod(world, run):
    """Daniel and a folder of his own in the sales pod.

    A folder per run, not a pod per scenario. The sales pod is where Vantage
    Freight actually keeps its files, so these scenarios run against one that
    already has other people's — and other runs' — work in it. Uploading
    `notes.txt` to its root would collide with whoever got there first, which is
    also what would happen to a person doing it.
    """
    daniel = await world.person("daniel")
    the_pod = await daniel.works_in("sales")
    home = f"/{run.name('files')}"
    await daniel.creates_a_folder(at_path=home, in_pod=the_pod)
    return daniel, the_pod, home


@scenario("A person uploads a file and it lands where they put it")
@proves("PS-DATA-030")
@covers("file.upload", "file.get", "file.list", "document.added")
async def test_a_file_lands_where_it_was_put(pod):
    alice, the_pod, home = pod

    uploaded = await alice.uploads(
        content=b"hello from a scenario", named="notes.txt", directory=home,
        in_pod=the_pod,
    )

    found = await alice.opens_file(uploaded["path"], in_pod=the_pod)
    assert found["name"] == "notes.txt", found
    # The tree rather than the file list: the list answers for the root
    # directory only, so a file inside a folder is invisible to it.
    assert uploaded["path"] in str(await alice.file_tree_of(the_pod))


@scenario("A person uploads into a folder they created")
@proves("PS-DATA-030", "PS-DATA-031")
@covers("file.folder.create", "file.upload", "file.children.list")
async def test_a_file_lands_in_a_folder(pod):
    alice, the_pod, home = pod
    reports = f"{home}/reports"
    await alice.creates_a_folder(at_path=reports, in_pod=the_pod)

    await alice.uploads(
        content=b"q3 numbers", named="q3.txt", directory=reports, in_pod=the_pod
    )

    found = await alice.opens_file(f"{reports}/q3.txt", in_pod=the_pod)
    assert found["name"] == "q3.txt", found


@scenario("A person browses the pod's files as a tree")
@proves("PS-DATA-031")
@covers("file.tree", "file.list")
async def test_the_file_tree_is_browsable(pod):
    alice, the_pod, home = pod
    docs = f"{home}/docs"
    await alice.creates_a_folder(at_path=docs, in_pod=the_pod)
    uploaded = await alice.uploads(
        content=b"a", named="a.txt", directory=docs, in_pod=the_pod
    )

    tree = await alice.file_tree_of(the_pod)

    assert tree is not None
    assert uploaded["path"] in str(tree), tree


@scenario("The original bytes come back exactly as they went in")
@proves("PS-DATA-041")
@covers("file.upload", "file.download")
async def test_the_original_bytes_survive(pod):
    alice, the_pod, home = pod
    content = b"\x00\x01binary-ish content \xc3\xa9 and text"

    uploaded = await alice.uploads(
        content=content, named="payload.bin", directory=home, in_pod=the_pod,
        content_type="application/octet-stream",
    )

    assert await alice.downloads(uploaded["path"], in_pod=the_pod) == content


@scenario("A person deletes a file and it stops being listed")
@proves("PS-DATA-032")
@covers("file.delete", "file.list", "file.get")
async def test_deleting_a_file_removes_it(pod):
    alice, the_pod, home = pod
    uploaded = await alice.uploads(
        content=b"temporary", named="temp.txt", directory=home, in_pod=the_pod
    )

    await alice.deletes_file(uploaded["path"], in_pod=the_pod)

    assert uploaded["path"] not in str(await alice.file_tree_of(the_pod))
    await alice.is_refused_file(uploaded["path"], in_pod=the_pod)


@scenario("A person gets a link to a file that works without a session")
@proves("PS-DATA-050")
@covers("file.signed_url", "file.upload")
async def test_a_signed_link_is_issued(pod):
    alice, the_pod, home = pod
    uploaded = await alice.uploads(
        content=b"shareable", named="share.txt", directory=home, in_pod=the_pod
    )

    link = await alice.signed_link_to(uploaded["path"], in_pod=the_pod)

    assert link, link
    assert any(str(v).startswith("http") or "/s/" in str(v) for v in link.values()), link


@scenario("Someone outside the pod cannot read its files")
@proves("PS-DATA-030")
@covers("file.get", "file.list")
async def test_an_outsider_cannot_read_files(world, pod):
    alice, the_pod, home = pod
    uploaded = await alice.uploads(
        content=b"private", named="secret.txt", directory=home, in_pod=the_pod
    )

    # Hannah works at Calder Retail, a different company altogether. She is
    # refused because she genuinely works somewhere else, not because a flag
    # says so — which is the only way this scenario means anything.
    outsider = await world.person("hannah")

    await outsider.is_refused_file(uploaded["path"], in_pod=the_pod)
