"""Required public-boundary document processing journeys with real workers."""

from __future__ import annotations

import asyncio

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
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict | None = None
    while asyncio.get_running_loop().time() < deadline:
        last = await api.get_file(path)
        if last["status"] in expected:
            return last
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"file {path} did not reach {sorted(expected)}; last response was {last}"
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
async def test_kreuzberg_upload_runs_outbox_worker_projection_search_and_dedup(
    pod_api: DatastoreApi,
    db_manager,
    document_worker,
    fake_document_processor_server: FakeDocumentProcessorServer,
):
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
        children = await pod_api.list_children(primary["path"])
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

        # Redis redelivery carries the same durable event id. The inbox must
        # acknowledge it without creating another extraction/job side effect.
        event = await _outbox_event_for_file(db_manager, primary["id"])
        bus = get_message_bus()
        await bus.publish(event.stream, event.payload)
        await bus.publish(event.stream, event.payload)
        await asyncio.sleep(0.5)
        assert fake_document_processor_server.requests["kreuzberg:success.pdf"] == 1


@pytest.mark.asyncio
async def test_kreuzberg_terminal_failures_are_durable_and_secret_safe(
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

        for item in (malformed, provider_error):
            failed = await _wait_for_status(pod_api, item["path"], {"FAILED"})
            assert failed["last_processing_error"].endswith(
                "document processing failed"
            )
            assert "CANARY_DATASTORE_PROVIDER_SECRET" not in str(failed)
            assert (await pod_api.list_children(item["path"]))["items"] == []


@pytest.mark.asyncio
async def test_docling_and_markitdown_adapters_run_through_http_outbox_and_worker(
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

    async with document_worker("markitdown"):
        markitdown = await pod_api.upload_file(
            "markitdown-success.html",
            b"Hermetic MarkItDown source",
            content_type="text/html",
        )
        markitdown_failure = await pod_api.upload_file(
            "markitdown-failure.html",
            b"FAIL with a provider payload",
            content_type="text/html",
        )
        await _dispatch_outbox(db_manager)
        await _wait_for_status(pod_api, markitdown["path"], {"COMPLETED"})
        failed = await _wait_for_status(pod_api, markitdown_failure["path"], {"FAILED"})
        assert "CANARY_DATASTORE_PROVIDER_SECRET" not in str(failed)
        markitdown_children = await pod_api.list_children(markitdown["path"])
        markitdown_markdown = next(
            item
            for item in markitdown_children["items"]
            if item["name"] == "document.md"
        )
        content = await pod_api.child_content(markitdown_markdown["path"])
        assert b"MarkItDown output" in content
        assert b"Hermetic MarkItDown source" in content
