"""Required public-boundary document processing journeys with real workers."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.infrastructure.events.message_bus import get_message_bus
from app.core.infrastructure.events.models import DomainEventOutbox
from app.core.infrastructure.events.outbox import OutboxDispatcher
from app.modules.datastore.tests.e2e.harness import (
    DatastoreApi,
    build_pdf_bytes,
)
from app.modules.datastore.tests.e2e.fake_document_processors import (
    FakeDocumentProcessorServer,
)
from app.modules.test_support.e2e.waiters import wait_for_status

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def _dispatch_outbox(db_manager) -> None:
    dispatcher = OutboxDispatcher(
        db_manager.session_factory,
        get_message_bus(),
        poll_seconds=0.01,
    )
    while await dispatcher.dispatch_once():
        pass


async def _wait_for_status(
    api: DatastoreApi,
    path: str,
    expected: set[str],
    *,
    timeout_seconds: float = 60,
) -> dict:
    # failed=set(): several callers below wait FOR "FAILED" as their own
    # expected terminus (the malformed/provider-error/docling/xberg failure
    # cases) -- wait_for_status's default fail-fast set ({"FAILED", "ERROR"})
    # would pytest.fail the instant that status is reached, before it could
    # ever be returned to the caller.
    return await wait_for_status(
        label=f"file {path} to reach {sorted(expected)}",
        probe=lambda: api.get_file(path),
        expected=expected,
        failed=set(),
        timeout_seconds=timeout_seconds,
        interval_seconds=0.1,
    )


async def _outbox_event_for_file(db_manager, file_id: str) -> DomainEventOutbox:
    async with db_manager.session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(DomainEventOutbox).where(
                        DomainEventOutbox.event_type == "datastore.file.created"
                    )
                )
            ).all()
        )
    return next(row for row in rows if row.payload.get("file_id") == file_id)


@pytest.mark.asyncio
async def test_kreuzberg_upload_indexes_a_document_and_makes_it_searchable(
    pod_api: DatastoreApi,
    db_manager,
    document_worker,
    fake_document_processor_server: FakeDocumentProcessorServer,
):
    """One document, end to end: upload, extract, project, search, dedup.

    The PR-lane half of what used to be a single 130-second test — the slowest
    in the entire e2e suite, 5.6% of its wall-clock on its own. It uploaded
    five PDFs to exercise five different extractor behaviours and searched
    three ways, which is a matrix, not a journey.

    What every pull request needs to know is that the pipeline is connected:
    an uploaded PDF reaches the extractor, its children get written, the
    projection lands, search finds it, and a redelivered event does not
    duplicate the work. That is this test, on one document.

    The extractor-behaviour matrix — config fallback, connection retry, chunk
    fallback, pages-only, and all three search methods — is
    test_kreuzberg_extractor_behaviour_matrix below, marked `slow` so it runs
    in the scheduled protected lane instead of in front of the merge button.
    """
    async with document_worker("kreuzberg"):
        uploaded = await pod_api.upload_file(
            "success.pdf",
            build_pdf_bytes("Original source for success.pdf"),
            content_type="application/pdf",
        )

        await _dispatch_outbox(db_manager)
        completed = await _wait_for_status(pod_api, uploaded["path"], {"COMPLETED"})
        assert completed["metadata"]["page_count"] == 1

        children = await pod_api.list_children(uploaded["path"])
        child_by_name = {item["name"]: item for item in children["items"]}
        assert {"document.md", "figure.png"} <= set(child_by_name)
        markdown = await pod_api.child_content(child_by_name["document.md"]["path"])
        assert b"Deterministic extracted content for success.pdf" in markdown
        assert b"<!-- PAGE 1 -->" in markdown
        assert await pod_api.child_content(child_by_name["figure.png"]["path"])
        page = next(item for item in children["items"] if item["kind"] == "page")
        rendered_page = await pod_api.child_content(page["path"])
        assert rendered_page.startswith(b"\xff\xd8")
        page_markdown = await pod_api.child_content(
            child_by_name["document.md"]["path"],
            page_start=1,
            page_end=1,
        )
        assert page_markdown.startswith(b"<!-- PAGE 1 -->")

        # HYBRID rather than all three: it is the only method that exercises
        # both the text index and the vector index, so one call covers the
        # projection paths a broken pipeline would take down. TEXT and VECTOR
        # in isolation are in the matrix test.
        search = await pod_api.search_files(
            "Deterministic extracted content for success.pdf",
            search_method="HYBRID",
        )
        assert uploaded["id"] in {item["file_id"] for item in search["items"]}
        hit = next(
            item for item in search["items"] if item["file_id"] == uploaded["id"]
        )
        assert hit["page_number"] == 1

        assert fake_document_processor_server.requests["kreuzberg:success.pdf"] == 1

        # Redis redelivery carries the same durable event id. The inbox must
        # acknowledge it without creating another extraction/job side effect.
        event = await _outbox_event_for_file(db_manager, uploaded["id"])
        bus = get_message_bus()
        await bus.publish(event.stream, event.payload)
        await bus.publish(event.stream, event.payload)
        await asyncio.sleep(0.5)
        assert fake_document_processor_server.requests["kreuzberg:success.pdf"] == 1


# `slow` keeps this out of the fast lane every PR runs and puts it in the
# scheduled protected run (backend-protected-e2e.yml selects `slow`). The
# behaviours below are extractor-adapter variations, not pipeline wiring:
# nothing here can break without the wiring test above also failing, so a
# nightly signal is the right cadence for them.
@pytest.mark.slow
@pytest.mark.timeout(240)
@pytest.mark.asyncio
async def test_kreuzberg_extractor_behaviour_matrix(
    pod_api: DatastoreApi,
    db_manager,
    document_worker,
    fake_document_processor_server: FakeDocumentProcessorServer,
):
    """Every extractor behaviour the Kreuzberg adapter has to absorb."""
    async with document_worker("kreuzberg"):
        files = []
        for name in (
            "success.pdf",
            "config-fallback.pdf",
            "connection-retry.pdf",
            "chunk-fallback.pdf",
            "pages-only.pdf",
        ):
            files.append(
                await pod_api.upload_file(
                    name,
                    build_pdf_bytes(f"Original source for {name}"),
                    content_type="application/pdf",
                )
            )

        await _dispatch_outbox(db_manager)
        completed = [
            await _wait_for_status(pod_api, item["path"], {"COMPLETED"})
            for item in files
        ]
        assert all(item["metadata"]["page_count"] == 1 for item in completed)

        primary = files[0]
        for search_method in ("TEXT", "VECTOR", "HYBRID"):
            search = await pod_api.search_files(
                "Deterministic extracted content for success.pdf",
                search_method=search_method,
            )
            assert primary["id"] in {item["file_id"] for item in search["items"]}, (
                search_method
            )
            primary_hit = next(
                item for item in search["items"] if item["file_id"] == primary["id"]
            )
            assert primary_hit["page_number"] == 1

        # A 400/422 makes the adapter retry once with the compatibility config;
        # a dropped connection makes it retry once on a fresh one. Both must
        # still land exactly one successful extraction.
        assert fake_document_processor_server.requests["kreuzberg:success.pdf"] == 1
        assert (
            fake_document_processor_server.requests["kreuzberg:config-fallback.pdf"]
            == 2
        )
        assert (
            fake_document_processor_server.requests["kreuzberg:connection-retry.pdf"]
            == 2
        )
        assert fake_document_processor_server.requests["kreuzberg:chunk"] == 1


@pytest.mark.asyncio
async def test_failure_kind_decides_terminal_vs_retry_and_never_leaks_secrets(
    pod_api: DatastoreApi,
    db_manager,
    document_worker,
):
    async with document_worker("kreuzberg"):
        malformed = await pod_api.upload_file(
            "malformed.pdf",
            build_pdf_bytes("Malformed processor response"),
            content_type="application/pdf",
        )
        provider_error = await pod_api.upload_file(
            "provider-error.pdf",
            build_pdf_bytes("Provider failure"),
            content_type="application/pdf",
        )
        await _dispatch_outbox(db_manager)

        # A response we cannot make sense of is a DOCUMENT-level failure: the
        # extractor answered, we just can't use the answer. It spends an attempt
        # and goes terminal so a poison file can't loop forever.
        failed = await _wait_for_status(pod_api, malformed["path"], {"FAILED"})
        assert failed["last_processing_error"].endswith("document processing failed")
        assert "CANARY_DATASTORE_PROVIDER_SECRET" not in str(failed)
        assert (await pod_api.list_children(malformed["path"]))["items"] == []

        # A 5xx is INFRASTRUCTURE unavailability — nothing was learned about the
        # document. It must go back to PENDING with its attempt refunded, or an
        # extractor outage would burn the 3-attempt budget and permanently fail
        # perfectly good user documents.
        released = await _wait_for_status(pod_api, provider_error["path"], {"PENDING"})
        assert released["processing_attempts"] == 0, (
            "a 5xx from the extractor must not spend the file's retry budget"
        )
        assert released["status"] != "FAILED_PERMANENT"
        # The upstream body carries a credential; it must never be persisted.
        assert "CANARY_DATASTORE_PROVIDER_SECRET" not in str(released)
        assert (await pod_api.list_children(provider_error["path"]))["items"] == []


@pytest.mark.asyncio
async def test_docling_adapter_runs_through_http_outbox_and_worker(
    pod_api: DatastoreApi,
    db_manager,
    document_worker,
):
    async with document_worker("docling"):
        docling = await pod_api.upload_file(
            "docling-success.html",
            b"<h1>Docling source</h1>",
            content_type="text/html",
        )
        docling_failure = await pod_api.upload_file(
            "docling-failure.html",
            b"<h1>Failure source</h1>",
            content_type="text/html",
        )
        docling_malformed = await pod_api.upload_file(
            "docling-malformed.html",
            b"<h1>Malformed result source</h1>",
            content_type="text/html",
        )
        docling_submit_error = await pod_api.upload_file(
            "docling-submit-error.html",
            b"<h1>Submit error source</h1>",
            content_type="text/html",
        )
        await _dispatch_outbox(db_manager)
        await _wait_for_status(pod_api, docling["path"], {"COMPLETED"})
        for failed_file in (
            docling_failure,
            docling_malformed,
            docling_submit_error,
        ):
            await _wait_for_status(pod_api, failed_file["path"], {"FAILED"})
        docling_children = await pod_api.list_children(docling["path"])
        docling_markdown = next(
            item for item in docling_children["items"] if item["name"] == "document.md"
        )
        content = await pod_api.child_content(docling_markdown["path"])
        assert b"Docling output for docling-success.html" in content
        assert b"<!-- PAGE 1 -->" in content
        assert b"<!-- PAGE 2 -->" in content

    # xberg has its own journey below, against the real wheel. It is not a
    # service with a URL, so there is nothing here worth doubling: the double
    # this used to run accepted every call, which is precisely how it certified
    # an `ExtractInput` the real extractor rejects outright.


@pytest.mark.asyncio
async def test_desktop_local_journey_converts_and_indexes_with_the_real_xberg_wheel(
    pod_api: DatastoreApi,
    db_manager,
    document_worker,
):
    """The desktop and local install's document path, with nothing doubled.

    This configuration has no counterpart in cloud: cloud converts in a
    Kreuzberg container reached over HTTP, and a local install has no container
    fleet, so it converts in the backend process through the real ``xberg``
    wheel. Every other document E2E exercises the HTTP adapter, so until this
    existed the in-process adapter's only coverage was against a hand-written
    double.

    That double is why this test exists. It accepted any ``ExtractInput``, so it
    certified a call that omitted ``mime_type`` and ``filename`` -- and the real
    wheel, handed a URI whose path is an extensionless temp file, cannot infer a
    type and refuses. Every upload on desktop failed with
    ``RuntimeError: document processing failed`` and pod search silently went
    empty. A double cannot catch that; only the wheel can.

    So the assertions below are about *real extraction*: text that only a real
    PDF parser produces, page markers derived from real page boundaries, and a
    search hit for a phrase that exists nowhere but inside the document.
    """
    fixture = Path(__file__).resolve().parents[2] / "tests/fixtures/arxiv/seq2seq.pdf"
    pdf_bytes = fixture.read_bytes()

    async with document_worker("xberg"):
        pdf = await pod_api.upload_file(
            "real-paper.pdf", pdf_bytes, content_type="application/pdf"
        )
        markdown = await pod_api.upload_file(
            "notes.md",
            b"# Field notes\n\nThe quokka telemetry handshake completed.\n",
            content_type="text/markdown",
        )
        html = await pod_api.upload_file(
            "page.html", b"<h1>Hermetic source</h1>", content_type="text/html"
        )
        # Genuinely corrupt, not a magic string a double agreed to reject: the
        # real extractor fails on this with a parse error from its Rust core.
        corrupt = await pod_api.upload_file(
            "corrupt.pdf",
            b"this is definitely not a pdf",
            content_type="application/pdf",
        )
        await _dispatch_outbox(db_manager)

        await _wait_for_status(pod_api, pdf["path"], {"COMPLETED"})
        await _wait_for_status(pod_api, markdown["path"], {"COMPLETED"})
        await _wait_for_status(pod_api, html["path"], {"COMPLETED"})
        failed = await _wait_for_status(pod_api, corrupt["path"], {"FAILED"})

        # A failure must stay terminal and must not carry the extractor's own
        # words into a stored, user-visible field.
        assert "document processing failed" in str(failed.get("last_processing_error"))
        assert "pdf_oxide" not in str(failed)

        children = await pod_api.list_children(pdf["path"])
        document_md = next(
            item for item in children["items"] if item["name"] == "document.md"
        )
        converted = (await pod_api.child_content(document_md["path"])).decode(
            "utf-8", "replace"
        )

        # Text only a real parser produces. The double emitted the source bytes
        # back under a heading, so any assertion of this kind passed there
        # whether or not extraction worked.
        assert "sequence" in converted.lower()
        assert len(converted) > 5_000, f"suspiciously short: {len(converted)}"
        # Page boundaries are reconstructed from the wheel's per-page content;
        # a single marker proves the reconstruction ran at all.
        assert "<!-- PAGE 1 -->" in converted

        # Indexing is the half users actually notice: this is what came back
        # empty on desktop, indistinguishable from "no matches".
        hits = await pod_api.search_files(query="quokka telemetry handshake")
        assert any(item["path"] == markdown["path"] for item in hits["items"]), (
            f"markdown not indexed; got {[i['path'] for i in hits['items']]}"
        )

        # A recursive listing must be able to tell those two apart. It used to
        # carry only path/name/kind/visibility, so a folder whose documents had
        # all failed to convert looked exactly like a healthy one -- and the
        # empty search over them had nothing to explain it. The flat listing has
        # always reported these, and the base instructions promise listings do.
        nodes: dict[str, dict] = {}

        def collect(node: dict) -> None:
            nodes[node["path"]] = node
            for child in node.get("children", []):
                collect(child)

        collect((await pod_api.tree(files_per_directory=20))["tree"])

        assert nodes[pdf["path"]]["status"] == "COMPLETED"
        assert nodes[pdf["path"]]["indexed"] is True
        assert nodes[pdf["path"]]["has_markdown"] is True
        assert nodes[corrupt["path"]]["status"] == "FAILED"
        assert nodes[corrupt["path"]]["indexed"] is False
