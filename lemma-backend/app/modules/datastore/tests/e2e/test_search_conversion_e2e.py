"""E2E tests for datastore file search and document conversion.

The conversion path runs against ONE real arXiv paper (the lightest,
``seq2seq``, under tests/fixtures/arxiv/) so conversion + full-text/vector/hybrid
search exercise genuine extracted text. The paper is paired in ``harness.PAPERS``
with a needle phrase that Kreuzberg reliably extracts (verified empirically), so
search assertions stay deterministic.

Real-PDF extraction is deliberately minimized to a single light paper across the
whole module: the session-scoped Kreuzberg test container has no restart policy,
and extracting several figure-heavy real PDFs OOM-crashes it, after which every
later extraction (even tiny ``.md`` files in other tests) fails with
ConnectionRefused. The directory-scope matrix is therefore driven by lightweight
``.md`` files — PATH-based scoping needs no real PDF content, ``.md`` files index
fast (no OCR/image extraction), and their needles are fully controlled.

Coverage:
  - CONVERSION: the real PDF exposes hidden child files — ``document.md`` (needle
    + native page markers), figure assets, and on-demand page renders — fetched
    full and page-wise by their ``<source-path>/<artifact>`` paths; a ``.md``
    file has no derived children.
  - SEARCH (TEXT/VECTOR/HYBRID): the paper's needle returns its file id.
  - DIRECTORY-SCOPED SEARCH: the PDF plus ``.md`` notes laid across a folder
    tree, with SUBTREE vs DIRECT scoping asserted deterministically by querying
    each in-scope file's own needle (matches driven by folder scope, not noise).
  - CONSISTENCY THROUGH MUTATION: move follows search; folder delete removes
    files from search.
  - LITERAL wildcard-char scope paths and personal /me-file searchability.

The conversion / scoped-search / move-delete assertions share a single indexed
corpus and form a step-N-depends-on-N-1 chain, so they live in one named test
rather than re-indexing the (slow, memory-heavy) real PDF per assertion.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.tests.e2e.harness import (
    PAPERS,
    DatastoreApi,
    index_file,
    load_paper,
)

pytestmark = pytest.mark.e2e


def _file_ids(search_result: dict) -> set[str]:
    return {item["file_id"] for item in search_result.get("items", [])}


class TestDatastoreSearchAndConversion:
    @pytest.mark.asyncio
    async def test_real_papers_convert_and_are_directory_scoped_through_mutation(
        self,
        pod_api: DatastoreApi,
        index_datastore_file,
    ):
        """A real arXiv paper laid across a folder tree converts to markdown and is searchable, with folder-scoped search staying correct through move and folder delete.

        To keep the shared session-scoped Kreuzberg container alive across the
        whole module, this exercises EXACTLY ONE real PDF — the lightest
        ``seq2seq`` (figure-heavy real PDFs OOM-crash the container, which then
        refuses every later extraction). The rest of the directory-scope matrix
        rides on lightweight ``.md`` files: PATH-based scoping does not need real
        PDF content, ``.md`` files index fast (no OCR/image extraction), and
        their needles are fully controlled, so the scope assertions stay
        deterministic without further straining Kreuzberg.
        """
        seq2seq_paper = PAPERS["seq2seq"]

        await pod_api.create_folder("/research")
        await pod_api.create_folder("/research/transformers")
        await pod_api.create_folder("/operations")

        # Corpus laid across the tree (one real PDF + lightweight .md notes):
        #   /research/transformers/<seq2seq.pdf>   (the conversion+search subject)
        #   /research/<research-note.md>            (direct child of /research)
        #   /operations/<ops-note.md>               (direct child of /operations)
        seq2seq = await pod_api.upload_file(
            seq2seq_paper.filename,
            load_paper("seq2seq"),
            directory_path="/research/transformers",
            content_type="application/pdf",
        )
        research_note = await pod_api.upload_file(
            "research-note.md",
            b"ResearchScopeNeedle triage notes for the research directory.",
            directory_path="/research",
        )
        ops_note = await pod_api.upload_file(
            "ops-note.md",
            b"OpsScopeNeedle runbook notes for the operations directory.",
            directory_path="/operations",
        )

        for file_entity in (seq2seq, research_note, ops_note):
            await index_file(index_datastore_file, file_entity)

        # --- Conversion: the PDF exposes derived child files; the .md does not. ---
        # A non-converted .md has no derived children (empty list, not an error).
        note_children = await pod_api.list_children(research_note["path"])
        assert note_children["items"] == []

        children = await pod_api.list_children(seq2seq["path"])
        child_by_name = {child["name"]: child for child in children["items"]}
        assert "document.md" in child_by_name
        assert child_by_name["document.md"]["kind"] == "markdown"
        # Renderable pages are listed as page children (1-based).
        page_children = [c for c in children["items"] if c["kind"] == "page"]
        assert page_children, children
        assert page_children[0]["page_number"] == 1

        # Full converted markdown carries the needle + native page markers.
        markdown_path = child_by_name["document.md"]["path"]
        full_markdown = await pod_api.child_content(markdown_path)
        assert seq2seq_paper.needle.encode() in full_markdown
        assert b"<!-- PAGE 1 -->" in full_markdown

        # Page-wise markdown: page 1 only is a subset that still starts at PAGE 1.
        page1_markdown = await pod_api.child_content(
            markdown_path, page_start=1, page_end=1
        )
        assert b"<!-- PAGE 1 -->" in page1_markdown
        assert len(page1_markdown) <= len(full_markdown)

        # Page-wise rendered image: page 1 rasterizes to a JPEG on demand.
        page1_image = await pod_api.child_content(page_children[0]["path"])
        assert page1_image[:2] == b"\xff\xd8"  # JPEG magic

        # Any extracted figure asset is fetchable by its own child path.
        for image_child in [c for c in children["items"] if c["kind"] == "image"][:1]:
            asset = await pod_api.child_content(image_child["path"])
            assert asset, image_child

        # All of the above live under the single pod files/ root (one-prefix
        # backup): child paths are <source-path>/<artifact>.
        assert markdown_path == f"{seq2seq['path']}/document.md"

        # --- Search: every method finds the seq2seq paper by its needle. ---
        for method in ("TEXT", "VECTOR", "HYBRID"):
            results = await pod_api.search_files(
                seq2seq_paper.needle, search_method=method
            )
            assert seq2seq["id"] in _file_ids(results), (method, results)

        # The matching chunk of a converted PDF carries the page it came from, so
        # an agent can jump straight to the right page.
        text_results = await pod_api.search_files(
            seq2seq_paper.needle, search_method="TEXT"
        )
        seq2seq_hits = [
            item
            for item in text_results["items"]
            if item["file_id"] == seq2seq["id"]
        ]
        assert seq2seq_hits, text_results
        assert seq2seq_hits[0]["page_number"] is not None
        assert seq2seq_hits[0]["page_number"] >= 1

        # --- Directory-scoped search (folder scope, query = in-scope needle). ---
        # /operations does not contain the seq2seq paper, so its needle scoped
        # there must not match it.
        ops_scoped = await pod_api.search_files(
            seq2seq_paper.needle, scope_path="/operations"
        )
        assert seq2seq["id"] not in _file_ids(ops_scoped), ops_scoped

        # SUBTREE /research reaches the seq2seq paper in /research/transformers.
        research_subtree = await pod_api.search_files(
            seq2seq_paper.needle,
            scope_path="/research",
            scope_mode="SUBTREE",
        )
        assert seq2seq["id"] in _file_ids(research_subtree), research_subtree

        # DIRECT /research excludes the nested seq2seq paper...
        research_direct_seq2seq = await pod_api.search_files(
            seq2seq_paper.needle,
            scope_path="/research",
            scope_mode="DIRECT",
        )
        assert seq2seq["id"] not in _file_ids(research_direct_seq2seq), (
            research_direct_seq2seq
        )
        # ...but includes the research-note .md, a direct child of /research.
        research_direct_note = await pod_api.search_files(
            "ResearchScopeNeedle",
            scope_path="/research",
            scope_mode="DIRECT",
        )
        assert research_note["id"] in _file_ids(research_direct_note), (
            research_direct_note
        )

        # DIRECT /research/transformers includes the seq2seq paper.
        transformers_direct = await pod_api.search_files(
            seq2seq_paper.needle,
            scope_path="/research/transformers",
            scope_mode="DIRECT",
        )
        assert seq2seq["id"] in _file_ids(transformers_direct), transformers_direct

        # --- A file path is not a valid search scope. ---
        file_scope_error = await pod_api.search_files(
            seq2seq_paper.needle,
            scope_path=seq2seq["path"],
            expected_status=400,
        )
        assert "Path must point to a folder" in file_scope_error["message"]

        # --- Move re-homes the file and folder-scoped search follows it. ---
        # Before the move, /operations does not surface the seq2seq paper.
        moved = await pod_api.update_file(
            seq2seq["path"],
            new_path="/operations/seq2seq-for-ops.pdf",
        )
        moved_results = await pod_api.search_files(
            seq2seq_paper.needle, scope_path="/operations"
        )
        assert moved["id"] in _file_ids(moved_results), moved_results

        # --- Deleting the folder removes the moved file from search. ---
        await pod_api.delete_file("/operations")
        deleted_results = await pod_api.search_files(seq2seq_paper.needle)
        assert moved["id"] not in _file_ids(deleted_results), deleted_results

    @pytest.mark.asyncio
    async def test_file_url_round_trips_through_public_route(
        self,
        pod_api: DatastoreApi,
    ):
        """A signed file URL serves the original bytes through the unauthenticated public route."""
        from urllib.parse import parse_qs, urlparse

        content = b"hello signed url world"
        uploaded = await pod_api.upload_file(
            "link.txt", content, directory_path="/"
        )

        info = await pod_api.file_url(uploaded["path"])
        assert info["path"] == uploaded["path"]
        assert "token=" in info["url"]
        assert info["expires_at"]

        # Local storage hands back a tokenized /public route; fetch it directly on
        # the same app (no auth header) to prove the token authorizes the bytes.
        token = parse_qs(urlparse(info["url"]).query)["token"][0]
        response = await pod_api.client.get(
            "/public/datastore/files", params={"token": token}
        )
        assert response.status_code == 200, response.text
        assert response.content == content
        etag = f'"{uploaded["content_sha256"]}"'
        assert response.headers["etag"] == etag
        assert "immutable" in response.headers["cache-control"]

        not_modified = await pod_api.client.get(
            "/public/datastore/files",
            params={"token": token},
            headers={"If-None-Match": etag},
        )
        assert not_modified.status_code == 304
        assert not not_modified.content

        # A garbage token is rejected.
        bad = await pod_api.client.get(
            "/public/datastore/files", params={"token": "nope"}
        )
        assert bad.status_code == 403

    @pytest.mark.asyncio
    async def test_editing_content_and_reindexing_updates_search(
        self,
        pod_api: DatastoreApi,
        index_datastore_file,
    ):
        """Re-uploading a file's content and re-indexing makes it findable by the new text."""
        note = await pod_api.upload_file(
            "ops.md",
            b"Sprint planning and risk register reviews improve delivery.",
            directory_path="/",
        )
        await index_file(index_datastore_file, note)

        updated = await pod_api.update_file(
            note["path"],
            content=b"Release readiness and risk register planning changed.",
            filename="ops.md",
            search_enabled=True,
        )
        await index_file(index_datastore_file, updated)

        results = await pod_api.search_files("Release readiness")
        assert updated["id"] in _file_ids(results), results

    @pytest.mark.asyncio
    async def test_scope_paths_treat_wildcard_chars_literally(
        self,
        pod_api: DatastoreApi,
        index_datastore_file,
    ):
        """Underscore and percent in scope paths match literally (not as SQL wildcards) for search and list."""
        await pod_api.create_folder("/research_2026")
        await pod_api.create_folder("/researchX2026")
        await pod_api.create_folder("/research%2026")
        await pod_api.create_folder("/researchY2026")

        scoped_literal = await pod_api.upload_file(
            "literal.md",
            b"WildcardPathNeedle exact scoped research file",
            directory_path="/research_2026",
        )
        scoped_sibling = await pod_api.upload_file(
            "sibling.md",
            b"WildcardPathNeedle sibling should not leak",
            directory_path="/researchX2026",
        )
        percent_scoped_literal = await pod_api.upload_file(
            "percent-literal.md",
            b"PercentPathNeedle exact scoped research file",
            directory_path="/research%2026",
        )
        percent_scoped_sibling = await pod_api.upload_file(
            "percent-sibling.md",
            b"PercentPathNeedle sibling should not leak",
            directory_path="/researchY2026",
        )

        for file_entity in (
            scoped_literal,
            scoped_sibling,
            percent_scoped_literal,
            percent_scoped_sibling,
        ):
            await index_file(index_datastore_file, file_entity)

        literal_scope_results = await pod_api.search_files(
            "WildcardPathNeedle",
            scope_path="/research_2026",
        )
        literal_scope_ids = _file_ids(literal_scope_results)
        assert scoped_literal["id"] in literal_scope_ids
        assert scoped_sibling["id"] not in literal_scope_ids

        percent_scope_results = await pod_api.search_files(
            "PercentPathNeedle",
            scope_path="/research%2026",
        )
        percent_scope_ids = _file_ids(percent_scope_results)
        assert percent_scoped_literal["id"] in percent_scope_ids
        assert percent_scoped_sibling["id"] not in percent_scope_ids

        literal_listing = await pod_api.list_files(directory_path="/research_2026")
        assert {item["id"] for item in literal_listing["items"]} == {
            scoped_literal["id"]
        }
        percent_listing = await pod_api.list_files(directory_path="/research%2026")
        assert {item["id"] for item in percent_listing["items"]} == {
            percent_scoped_literal["id"]
        }

        await pod_api.delete_file("/research_2026")
        await pod_api.get_file(scoped_sibling["path"])
        await pod_api.delete_file("/research%2026")
        await pod_api.get_file(percent_scoped_sibling["path"])

    @pytest.mark.asyncio
    async def test_personal_files_are_searchable_by_their_owner(
        self,
        pod_api: DatastoreApi,
        index_datastore_file,
    ):
        """The owner's /me personal file surfaces in pod search with its /me path metadata."""
        personal = await pod_api.upload_file(
            "private.md",
            b"PrivateBetaSignal should never enter pod search.",
            directory_path="/me",
            search_enabled=True,
        )
        await index_file(index_datastore_file, personal)

        private_search = await pod_api.search_files("PrivateBetaSignal")
        assert private_search["items"][0]["file_id"] == personal["id"]
        assert private_search["items"][0]["path"] == "/me/private.md"
        assert private_search["items"][0]["metadata"]["path"] == "/me/private.md"
