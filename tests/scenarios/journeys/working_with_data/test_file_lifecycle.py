"""Working with data → moving files, linking to them, and reading what is in them."""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.waiting import eventually, never

pytestmark = [journey("Working with data"), capability("Put documents in")]


@pytest.fixture
async def pod_with_file(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    uploaded = await alice.uploads(
        content=b"the original contents", named="notes.txt", in_pod=pod
    )
    return alice, pod, uploaded


@scenario("A file keeps its identity when it moves")
@proves("PS-DATA-032")
@covers("file.update", "file.get", "file.get_by_id")
async def test_moving_a_file_keeps_its_identity(pod_with_file):
    alice, pod, uploaded = pod_with_file
    await alice.creates_a_folder(at_path="/archive", in_pod=pod)
    file_id = uploaded["id"]

    await alice.moves_file("/notes.txt", to="/archive/notes.txt", in_pod=pod)

    moved = await alice.opens_file("/archive/notes.txt", in_pod=pod)
    assert str(moved["id"]) == str(file_id), (
        "moving a file must not make it a different file"
    )
    by_id = await alice.opens_file_by_id(str(file_id), in_pod=pod)
    assert str(by_id["id"]) == str(file_id), by_id


@scenario("A person gets a link to a file they can read")
@proves("PS-DATA-050")
@covers("file.url", "file.download")
async def test_a_file_has_a_link(pod_with_file):
    alice, pod, _uploaded = pod_with_file

    link = await alice.link_to("/notes.txt", in_pod=pod)

    assert link, link
    assert await alice.downloads("/notes.txt", in_pod=pod) == b"the original contents"


@scenario("A person lists what is inside a folder")
@proves("PS-DATA-031")
@covers("file.tree", "file.folder.create")
async def test_a_folder_lists_its_contents(pod_with_file):
    alice, pod, _uploaded = pod_with_file
    await alice.creates_a_folder(at_path="/reports", in_pod=pod)
    await alice.uploads(
        content=b"q1", named="q1.txt", directory="/reports", in_pod=pod
    )

    tree = await alice.file_tree_of(pod)

    assert "q1.txt" in str(tree), tree


@scenario("An unavailable converter does not count against the document")
@proves("PS-DATA-041")
@covers("file.upload", "file.get")
async def test_an_unavailable_converter_does_not_burn_attempts(pod_with_file):
    alice, pod, _uploaded = pod_with_file

    await alice.uploads(
        content=b"%PDF-1.4 a document with words in it",
        named="report.pdf", in_pod=pod, content_type="application/pdf",
        searchable=True,
    )

    # This stack runs no extraction service, which is the case the promise is
    # about: the document must stay queued for a later attempt rather than
    # being marked failed, and the unavailable dependency must not be counted
    # against the file's retry budget. Both are checked, because failing either
    # way is how a transient outage turns into permanently unreadable documents.
    settled = await never_becomes_failed(alice, pod, "/report.pdf")
    assert settled["status"] == "PENDING", settled
    assert settled["processing_attempts"] == 0, (
        f"an unavailable converter must not count as an attempt: {settled}"
    )
    assert settled["last_processing_error"] is None, settled


async def never_becomes_failed(alice, pod, path: str, *, within: float = 12.0):
    """Watch a file for a while and fail if it is ever marked failed."""
    last = None
    await never(
        lambda: alice.opens_file(path, in_pod=pod),
        lambda f: str(f.get("status")).startswith("FAILED"),
        describe=f"{path} being marked failed while the converter is unavailable",
        within=within,
    )
    last = await alice.opens_file(path, in_pod=pod)
    return last


@scenario("A person supplies their own readable text for a document")
@proves("PS-DATA-040")
@covers("file.markdown.attach", "file.markdown.detach", "file.children.list",
        "file.child.get")
async def test_supplied_markdown_is_used(pod_with_file):
    alice, pod, _uploaded = pod_with_file
    # Supplying markdown is the escape hatch from extraction: the person has
    # the text already, so the platform does not need to derive it. That makes
    # it the one document path a deployment without a converter still has.
    await alice.uploads(
        content=b"%PDF-1.4 pretend document",
        named="supplied.pdf", in_pod=pod, content_type="application/pdf",
        # Indexing on: attaching to a file with indexing off is accepted and
        # then silently discarded. See DEV-DATA-001.
        searchable=True,
    )

    await alice.attaches_markdown(
        "# Notes\n\nWhat this file actually says.",
        to_path="/supplied.pdf", in_pod=pod,
    )

    children = await eventually(
        lambda: alice.children_of("/supplied.pdf", in_pod=pod),
        lambda items: bool(items),
        describe="the supplied markdown to be stored as a child",
        timeout=45.0,
    )
    assert any(c["name"] == "document.md" for c in children), children

    derived = await alice.reads_derived_markdown("/supplied.pdf", in_pod=pod)
    assert derived.status_code == 200, derived.text[:300]
    assert "What this file actually says" in derived.text, derived.text[:300]

    await alice.detaches_markdown(from_path="/supplied.pdf", in_pod=pod)


@scenario("Supplying text for a file that is not indexed is refused, not discarded")
@proves("PS-DATA-040")
@covers("file.markdown.attach", "file.children.list")
@pytest.mark.xfail(
    reason="DEV-DATA-001: attach answers 200 and stores nothing when indexing is off",
    strict=True,
)
async def test_attaching_to_an_unindexed_file_is_refused(pod_with_file):
    alice, pod, _uploaded = pod_with_file
    await alice.uploads(
        content=b"%PDF-1.4 pretend document",
        named="unindexed.pdf", in_pod=pod, content_type="application/pdf",
    )

    response = await alice.api.call(
        "PUT",
        f"/pods/{pod['id']}/datastore/files/by-path/markdown",
        files={
            "path": (None, "/unindexed.pdf"),
            "data": ("c.md", b"# Notes\n\nSupplied.", "text/markdown"),
        },
    )

    # Either refuse it, or store it. Answering 200 and keeping nothing is the
    # one outcome a person cannot act on.
    if response.status_code < 400:
        children = await alice.children_of("/unindexed.pdf", in_pod=pod)
        assert children, (
            "attach reported success but stored nothing, and nothing tells the "
            "person their text was dropped"
        )


@scenario("A person searches what is in their documents")
@proves("PS-DATA-043")
@covers("file.search")
async def test_documents_are_searchable(pod_with_file):
    alice, pod, _uploaded = pod_with_file

    found = await alice.searches_files("original", in_pod=pod)

    assert found is not None, found
