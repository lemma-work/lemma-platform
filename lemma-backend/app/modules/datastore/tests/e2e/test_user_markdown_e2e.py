"""E2E tests for bring-your-own markdown (attach a user-authored markdown +
companion images to a document; detach to revert to extraction).

The ATTACH path is Kreuzberg-free by design — a file flagged ``markdown_source=
user`` is indexed from its stored ``source.md`` with no extraction — so the
core test uses a lightweight synthetic PDF and never strains the shared
Kreuzberg container. Only the DETACH revert re-extracts, so that test uses the
light real ``seq2seq`` paper (the one real PDF the module already relies on).
"""

from __future__ import annotations

import pytest

from app.modules.datastore.tests.e2e.harness import (
    PAPERS,
    DatastoreApi,
    build_pdf_bytes,
    index_file,
    load_paper,
)

pytestmark = pytest.mark.e2e

# Arbitrary bytes tagged as a PNG — storage/serving never validate the format.
_PNG = b"\x89PNG\r\n\x1a\nfake-diagram-bytes"


def _file_ids(search_result: dict) -> set[str]:
    return {item["file_id"] for item in search_result.get("items", [])}


class TestUserMarkdown:
    @pytest.mark.asyncio
    async def test_attach_markdown_with_companion_images_is_served_and_searchable(
        self,
        pod_api: DatastoreApi,
        index_datastore_file,
    ):
        # A digital PDF whose text-layer needle must NOT get indexed while the
        # user's markdown is the source of truth (BYO skips extraction).
        pdf = build_pdf_bytes("brontovyre lives only in the original PDF text layer.")
        doc = await pod_api.upload_file(
            "report.pdf", pdf, content_type="application/pdf"
        )

        user_md = (
            b"<!-- PAGE 1 -->\n\n# Curated\n\n"
            b"zephyrquokka only appears in the attached markdown.\n\n"
            b"![diagram](assets/fig1.png)\n"
        )
        await pod_api.attach_markdown(doc["path"], user_md, images=[("fig1.png", _PNG)])
        await index_file(index_datastore_file, doc)

        # document.md is the user markdown; the image ref is rewritten to the
        # sibling basename and the companion image is a listed child artifact.
        children = await pod_api.list_children(doc["path"])
        by_name = {child["name"]: child for child in children["items"]}
        assert by_name["document.md"]["kind"] == "markdown"
        assert by_name["fig1.png"]["kind"] == "image"

        markdown = await pod_api.child_content(by_name["document.md"]["path"])
        assert b"zephyrquokka" in markdown
        assert b"![diagram](fig1.png)" in markdown  # rewritten from assets/fig1.png

        served_image = await pod_api.child_content(by_name["fig1.png"]["path"])
        assert served_image == _PNG

        # Search sees the user's markdown, not the (never-extracted) PDF text.
        # TEXT (exact-token) search is used for presence/absence — vector/hybrid
        # always return nearest-neighbour chunks, so they can't prove absence.
        found = await pod_api.search_files("zephyrquokka", search_method="TEXT")
        assert doc["id"] in _file_ids(found)
        pdf_only = await pod_api.search_files("brontovyre", search_method="TEXT")
        assert doc["id"] not in _file_ids(pdf_only)

    @pytest.mark.asyncio
    async def test_detach_markdown_reverts_to_extraction(
        self,
        pod_api: DatastoreApi,
        index_datastore_file,
    ):
        seq2seq = PAPERS["seq2seq"]
        doc = await pod_api.upload_file(
            seq2seq.filename, load_paper("seq2seq"), content_type="application/pdf"
        )

        # Attach markdown → indexed from the markdown (extraction skipped). TEXT
        # (exact-token) search is used throughout: vector/hybrid always return
        # nearest-neighbour chunks, so they cannot prove a term is absent.
        await pod_api.attach_markdown(
            doc["path"],
            b"# Notes\n\nzephyrquokka is the attached-markdown needle.\n",
        )
        await index_file(index_datastore_file, doc)
        found = await pod_api.search_files("zephyrquokka", search_method="TEXT")
        assert doc["id"] in _file_ids(found)
        # The real paper's own text was not extracted while BYO was active.
        assert doc["id"] not in _file_ids(
            await pod_api.search_files(seq2seq.needle, search_method="TEXT")
        )

        # Detach → reprocess extracts the real PDF: user needle gone, paper text back.
        await pod_api.detach_markdown(doc["path"])
        await index_file(index_datastore_file, doc)

        gone = await pod_api.search_files("zephyrquokka", search_method="TEXT")
        assert doc["id"] not in _file_ids(gone)
        extracted = await pod_api.search_files(seq2seq.needle, search_method="TEXT")
        assert doc["id"] in _file_ids(extracted)
