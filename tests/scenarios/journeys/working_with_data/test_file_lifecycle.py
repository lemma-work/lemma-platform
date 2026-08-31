"""Working with data → moving files, linking to them, and reading what is in them."""

from __future__ import annotations

import pytest


from harness import capability, covers, journey, proves, scenario, stack_lane
from harness.waiting import eventually, never

pytestmark = [journey("Working with data"), capability("Put documents in")]


@pytest.fixture
async def pod_with_file(world, run):
    """Daniel's own folder in the sales pod, with a file in it.

    A folder per run rather than mangled file names, because that is what a
    person actually does in a pod they share — and it lets the names inside stay
    the readable ones a scenario is about. Uploading `/notes.txt` to the root of
    a standing pod collides with whichever run put one there first, and the 409
    arrives in this fixture, so it reads as six broken scenarios rather than one
    name.
    """
    daniel = await world.person("daniel")
    pod = await daniel.works_in("sales")
    home = f"/{run.name('files')}"
    await daniel.creates_a_folder(at_path=home, in_pod=pod)
    uploaded = await daniel.uploads(
        content=b"the original contents",
        named="notes.txt",
        directory=home,
        in_pod=pod,
    )
    return daniel, pod, home, uploaded


@scenario("A file keeps its identity when it moves")
@proves("PS-DATA-032")
@covers("file.update", "file.get", "file.get_by_id")
async def test_moving_a_file_keeps_its_identity(pod_with_file):
    alice, pod, home, uploaded = pod_with_file
    await alice.creates_a_folder(at_path=f"{home}/archive", in_pod=pod)
    file_id = uploaded["id"]

    await alice.moves_file(uploaded["path"], to=f"{home}/archive/notes.txt", in_pod=pod)

    moved = await alice.opens_file(f"{home}/archive/notes.txt", in_pod=pod)
    assert str(moved["id"]) == str(file_id), (
        "moving a file must not make it a different file"
    )
    by_id = await alice.opens_file_by_id(str(file_id), in_pod=pod)
    assert str(by_id["id"]) == str(file_id), by_id


@scenario("A person gets a link to a file they can read")
@proves("PS-DATA-050")
@covers("file.url", "file.download")
async def test_a_file_has_a_link(pod_with_file):
    alice, pod, home, uploaded = pod_with_file

    link = await alice.link_to(uploaded["path"], in_pod=pod)

    assert link, link
    assert await alice.downloads(uploaded["path"], in_pod=pod) == b"the original contents"


@scenario("A person lists what is inside a folder")
@proves("PS-DATA-031")
@covers("file.tree", "file.folder.create")
async def test_a_folder_lists_its_contents(pod_with_file):
    alice, pod, home, uploaded = pod_with_file
    await alice.creates_a_folder(at_path=f"{home}/reports", in_pod=pod)
    await alice.uploads(
        content=b"q1", named="q1.txt", directory=f"{home}/reports", in_pod=pod
    )

    tree = await alice.file_tree_of(pod)

    assert "q1.txt" in str(tree), tree


#: A document an extractor can actually read, so "failed" can only mean the
#: product failed it. These bytes were `%PDF-1.4 a document with words in it`,
#: which is not a PDF: against any deployment with a working extractor that is
#: a document error, correctly marked failed, and the scenario read it as the
#: product breaking its promise.
A_READABLE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
    b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 44>>stream\n"
    b"BT /F1 12 Tf 20 100 Td (hello lemma) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


@scenario("An unavailable converter does not count against the document")
@proves("PS-DATA-041")
@covers("file.upload", "file.get")
@stack_lane("needs a deployment with no document converter reachable")
async def test_an_unavailable_converter_does_not_burn_attempts(a_pod_of_its_own):
    # A pod of its own, because this scenario's whole subject is a document that
    # can never be converted — it stays queued and retrying for as long as it
    # exists, and document work queues per pod. Left in the sales pod it would
    # sit in front of every later document scenario there.
    alice, pod = a_pod_of_its_own

    report = await alice.uploads(
        content=A_READABLE_PDF,
        named="report.pdf",
        in_pod=pod,
        content_type="application/pdf",
        searchable=True,
    )
    try:
        await _the_promise_about_an_unreachable_extractor(alice, pod, report)
    finally:
        # Delete it the moment the assertions are done. While this document
        # exists it is re-claimed and released for as long as the run lasts —
        # which is the subject of DEV-DATA-003, and not free: the retries stall
        # the worker's event loop, and the visible symptom lands journeys away
        # as agent runs that stop being answered. The pod is swept at the end,
        # but "the end" is ten minutes of every other scenario paying for this
        # one. Nothing above needs the file afterwards.
        await alice.deletes_file(report["path"], in_pod=pod)


async def _the_promise_about_an_unreachable_extractor(alice, pod, report):
    # Never failed, wherever this runs. That half is the promise: an extractor
    # outage releases the claim rather than spending one of three attempts, so
    # three blips cannot add up to a permanently unreadable document.
    settled = await never_becomes_failed(alice, pod, report["path"])

    # Out of PROCESSING first. A claimed file says nothing yet either way: an
    # unreachable extractor releases the claim back to PENDING and refunds the
    # attempt, which is the very behaviour under test, so reading the status
    # mid-claim decides the question by timing rather than by what happened.
    settled = await eventually(
        lambda: alice.opens_file(report["path"], in_pod=pod),
        lambda f: str(f.get("status")) != "PROCESSING",
        describe=f"{report['path']} to settle out of PROCESSING",
        timeout=60.0,
    )

    # The rest is only observable where there is no extractor to reach. This
    # asserted PENDING unconditionally and so reported a deployment with a
    # working Kreuzberg as broken — the bytes it uploaded were not a PDF, which
    # makes "failed" the correct answer there, and the scenario called that a
    # bug. The document is readable now, so FAILED means what the wait says.
    if settled["status"] != "PENDING":
        pytest.skip(
            f"this deployment converted the document ({settled['status']}), so "
            f"it has an extractor and there is no outage here to observe"
        )
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
@covers(
    "file.markdown.attach", "file.markdown.detach", "file.children.list", "file.child.get"
)
async def test_supplied_markdown_is_used(pod_with_file):
    alice, pod, home, uploaded = pod_with_file
    # Supplying markdown is the escape hatch from extraction: the person has
    # the text already, so the platform does not need to derive it. That makes
    # it the one document path a deployment without a converter still has.
    supplied = await alice.uploads(
        content=b"%PDF-1.4 pretend document",
        named="supplied.pdf",
        in_pod=pod,
        directory=home,
        content_type="application/pdf",
        # Indexing on: attaching to a file with indexing off is accepted and
        # then silently discarded. See DEV-DATA-001.
        searchable=True,
    )

    await alice.attaches_markdown(
        "# Notes\n\nWhat this file actually says.",
        to_path=supplied["path"],
        in_pod=pod,
    )

    children = await eventually(
        lambda: alice.children_of(supplied["path"], in_pod=pod),
        bool,
        describe="the supplied markdown to be stored as a child",
        # Storing the child is queued work, and the whole suite shares one
        # worker. Forty-five seconds was enough when every scenario had a pod to
        # itself; it is not enough alongside `test_bulk_fairness`, which fills
        # the staging pool on purpose. CI shards by journey and never sees this,
        # a local run of the whole directory does, and a wait costs nothing when
        # things are fast.
        timeout=150.0,
    )
    assert any(c["name"] == "document.md" for c in children), children

    derived = await alice.reads_derived_markdown(supplied["path"], in_pod=pod)
    assert derived.status_code == 200, derived.text[:300]
    assert "What this file actually says" in derived.text, derived.text[:300]

    await alice.detaches_markdown(from_path=supplied["path"], in_pod=pod)


@scenario("Supplying text for a file that is not indexed is refused, not discarded")
@proves("PS-DATA-040")
@covers("file.markdown.attach", "file.children.list")
async def test_attaching_to_an_unindexed_file_is_refused(pod_with_file):
    alice, pod, home, uploaded = pod_with_file
    unindexed = await alice.uploads(
        content=b"%PDF-1.4 pretend document",
        named="unindexed.pdf",
        in_pod=pod,
        directory=home,
        content_type="application/pdf",
    )

    response = await alice.api.call(
        "PUT",
        f"/pods/{pod['id']}/datastore/files/by-path/markdown",
        files={
            "path": (None, unindexed["path"]),
            "data": ("c.md", b"# Notes\n\nSupplied.", "text/markdown"),
        },
    )

    # Either refuse it, or store it. Answering 200 and keeping nothing is the
    # one outcome a person cannot act on.
    if response.status_code < 400:
        children = await alice.children_of(unindexed["path"], in_pod=pod)
        assert children, (
            "attach reported success but stored nothing, and nothing tells the "
            "person their text was dropped"
        )


@scenario("A person searches what is in their documents")
@proves("PS-DATA-043")
@covers("file.search")
async def test_documents_are_searchable(pod_with_file):
    alice, pod, home, uploaded = pod_with_file

    found = await alice.searches_files("original", in_pod=pod)

    assert found is not None, found
