"""Working with data → putting documents in, and getting them back out."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Working with data"), capability("Put documents in")]


@pytest.fixture
async def pod(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    return alice, await alice.creates_a_pod()


@scenario("A person uploads a file and it lands where they put it")
@proves("PS-DATA-030")
@covers("file.upload", "file.get", "file.list", "document.added")
async def test_a_file_lands_where_it_was_put(pod):
    alice, the_pod = pod

    await alice.uploads(content=b"hello from a scenario", named="notes.txt", in_pod=the_pod)

    found = await alice.opens_file("/notes.txt", in_pod=the_pod)
    assert found["name"] == "notes.txt", found
    assert "notes.txt" in {f["name"] for f in await alice.files_in(the_pod)}


@scenario("A person uploads into a folder they created")
@proves("PS-DATA-030", "PS-DATA-031")
@covers("file.folder.create", "file.upload", "file.children.list")
async def test_a_file_lands_in_a_folder(pod):
    alice, the_pod = pod
    await alice.creates_a_folder(at_path="/reports", in_pod=the_pod)

    await alice.uploads(
        content=b"q3 numbers", named="q3.txt", directory="/reports", in_pod=the_pod
    )

    found = await alice.opens_file("/reports/q3.txt", in_pod=the_pod)
    assert found["name"] == "q3.txt", found


@scenario("A person browses the pod's files as a tree")
@proves("PS-DATA-031")
@covers("file.tree", "file.list")
async def test_the_file_tree_is_browsable(pod):
    alice, the_pod = pod
    await alice.creates_a_folder(at_path="/docs", in_pod=the_pod)
    await alice.uploads(content=b"a", named="a.txt", directory="/docs", in_pod=the_pod)

    tree = await alice.file_tree_of(the_pod)

    assert tree is not None
    assert "a.txt" in str(tree), tree


@scenario("The original bytes come back exactly as they went in")
@proves("PS-DATA-041")
@covers("file.upload", "file.download")
async def test_the_original_bytes_survive(pod):
    alice, the_pod = pod
    content = b"\x00\x01binary-ish content \xc3\xa9 and text"

    await alice.uploads(content=content, named="payload.bin", in_pod=the_pod,
                        content_type="application/octet-stream")

    assert await alice.downloads("/payload.bin", in_pod=the_pod) == content


@scenario("A person deletes a file and it stops being listed")
@proves("PS-DATA-032")
@covers("file.delete", "file.list", "file.get")
async def test_deleting_a_file_removes_it(pod):
    alice, the_pod = pod
    await alice.uploads(content=b"temporary", named="temp.txt", in_pod=the_pod)

    await alice.deletes_file("/temp.txt", in_pod=the_pod)

    assert "temp.txt" not in {f["name"] for f in await alice.files_in(the_pod)}
    await alice.is_refused_file("/temp.txt", in_pod=the_pod)


@scenario("A person gets a link to a file that works without a session")
@proves("PS-DATA-050")
@covers("file.signed_url", "file.upload")
async def test_a_signed_link_is_issued(pod):
    alice, the_pod = pod
    await alice.uploads(content=b"shareable", named="share.txt", in_pod=the_pod)

    link = await alice.signed_link_to("/share.txt", in_pod=the_pod)

    assert link, link
    assert any(str(v).startswith("http") or "/s/" in str(v) for v in link.values()), link


@scenario("Someone outside the pod cannot read its files")
@proves("PS-DATA-030")
@covers("file.get", "file.list")
async def test_an_outsider_cannot_read_files(world, pod):
    alice, the_pod = pod
    await alice.uploads(content=b"private", named="secret.txt", in_pod=the_pod)

    outsider = await world.new_person("outsider")

    await outsider.is_refused_file("/secret.txt", in_pod=the_pod)
