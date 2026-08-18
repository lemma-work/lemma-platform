"""Unit tests for the pod toolset.

These cover the contract that matters to agents: the toolset is registered, a
read tool returns structured pod data, and a write the agent lacks a grant for
comes back as a ``needs_approval`` result (the hand-off to the approval gate)
rather than raising.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod import pydantic_adapter as pod_adapter
from app.modules.agent.tools.pod.models import (
    GetFileUrlRequest,
    PodGetRecordsRequest,
    PodReadFileRequest,
    PodTablesRequest,
    PodWriteFileRequest,
    PodWriteRecordRequest,
    SearchFilesRequest,
    ViewDocumentPagesRequest,
)
from app.modules.agent.tools.registry import resolve_agent_toolsets
from app.modules.datastore.domain.errors import DatastoreConflictError


def _run_ctx(
    *,
    pod_cwd: str | None = None,
    vision_mode: AgentVisionMode = AgentVisionMode.DIRECT,
) -> SimpleNamespace:
    # DIRECT by default: most pod tools are unaffected by vision, and the ones
    # that are were written against a model that reads images itself.
    return SimpleNamespace(
        deps=BaseAgentContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            vision_mode=vision_mode,
            pod_cwd=pod_cwd,
        )
    )


def _patch_services(monkeypatch, services) -> None:
    @asynccontextmanager
    async def fake_pod_services(deps):  # noqa: ANN001 - test stub
        del deps
        yield services

    monkeypatch.setattr(pod_adapter, "pod_services", fake_pod_services)


def test_pod_toolset_is_registered_under_pod_toolset_enum():
    toolsets = resolve_agent_toolsets([AgentToolset.POD])
    assert pod_adapter.pod_toolset in toolsets


def test_pod_toolset_exposes_exactly_the_ten_tools():
    names = set(pod_adapter.pod_toolset.tools.keys())
    assert names == {
        "pod_tables",
        "pod_get_records",
        "pod_write_record",
        "pod_query",
        "pod_list_files",
        "pod_read_file",
        "pod_write_file",
        "pod_view_document_pages",
        "pod_get_file_url",
        "pod_search_files",
    }


@pytest.mark.asyncio
async def test_pod_tables_lists_then_describes(monkeypatch):
    column = SimpleNamespace(
        name="id", type="UUID", required=True, description="Primary key"
    )
    table = SimpleNamespace(
        table_name="customers",
        description="Customer records",
        primary_key_column="id",
        enable_rls=False,
        columns=[column],
    )
    services = SimpleNamespace(
        table=SimpleNamespace(
            list_tables=AsyncMock(return_value=([table], None)),
            get_table=AsyncMock(return_value=table),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    # No table_name → list all tables with schemas.
    listed = await pod_adapter.pod_tables(_run_ctx(), PodTablesRequest())
    assert listed["success"] is True
    assert listed["tables"][0]["name"] == "customers"
    assert listed["tables"][0]["columns"][0]["name"] == "id"

    # table_name → describe that one.
    described = await pod_adapter.pod_tables(
        _run_ctx(), PodTablesRequest(table_name="customers")
    )
    assert described["success"] is True
    assert described["table"]["name"] == "customers"
    services.table.get_table.assert_awaited_once()


@pytest.mark.asyncio
async def test_pod_write_record_without_grant_asks_for_approval(monkeypatch):
    @asynccontextmanager
    async def denying_pod_services(deps):  # noqa: ANN001 - test stub
        del deps
        raise DomainError(
            "Missing permission datastore.record.write",
            code="MISSING_WORKLOAD_RESOURCE_GRANT",
            status_code=403,
        )
        yield  # pragma: no cover - unreachable, makes this an async generator

    monkeypatch.setattr(pod_adapter, "pod_services", denying_pod_services)

    result = await pod_adapter.pod_write_record(
        _run_ctx(),
        PodWriteRecordRequest(
            action="create", table_name="customers", data={"name": "Ada"}
        ),
    )

    assert result["success"] is False
    assert result["code"] == "MISSING_WORKLOAD_RESOURCE_GRANT"
    assert result["needs_approval"] is True
    # Approval re-targets the merged write tool (with its action in args).
    assert result["approval"]["tool_name"] == "pod_write_record"
    assert result["approval"]["args"]["action"] == "create"
    assert result["approval"]["args"]["table_name"] == "customers"


@pytest.mark.asyncio
async def test_pod_write_record_validates_required_fields(monkeypatch):
    # update/delete require record_id; create/update require data — caught before
    # touching services (so the AsyncMock is never awaited).
    services = SimpleNamespace(
        record=SimpleNamespace(update_record=AsyncMock(), delete_record=AsyncMock()),
        table=SimpleNamespace(get_table=AsyncMock()),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    missing_id = await pod_adapter.pod_write_record(
        _run_ctx(), PodWriteRecordRequest(action="delete", table_name="t")
    )
    assert missing_id["success"] is False
    assert "record_id" in missing_id["error"]
    services.record.delete_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_pod_write_record_rejects_empty_data(monkeypatch):
    # create/update with empty or all-null `data` must be rejected with a clear
    # error and must NOT touch the datastore — the silent blank-row bug.
    record = SimpleNamespace(data={"id": "1", "title": "real"})
    services = SimpleNamespace(
        record=SimpleNamespace(create_record=AsyncMock(return_value=record)),
        table=SimpleNamespace(get_table=AsyncMock()),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)
    monkeypatch.setattr(
        pod_adapter, "_table_context", AsyncMock(return_value=SimpleNamespace())
    )

    for payload in ({}, {"title": None}, {"title": "   "}):
        result = await pod_adapter.pod_write_record(
            _run_ctx(),
            PodWriteRecordRequest(action="create", table_name="t", data=payload),
        )
        assert result["success"] is False
        assert "non-empty" in result["error"]
    services.record.create_record.assert_not_awaited()

    # A real payload (incl. a nested object value) goes through to create_record.
    ok = await pod_adapter.pod_write_record(
        _run_ctx(),
        PodWriteRecordRequest(
            action="create",
            table_name="t",
            data={"title": "real", "meta": {"tags": ["a", "b"]}},
        ),
    )
    assert ok["success"] is True
    services.record.create_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_pod_write_record_accepts_json_string_data(monkeypatch):
    # Models on OpenAI-compatible providers commonly pass `data` as a JSON-encoded
    # string rather than a native object (the free-form object schema carries an
    # empty `properties` map, which weaker models fill with `{}`, dropping the
    # row). The string form must decode to a dict and write through unchanged.
    record = SimpleNamespace(data={"id": "1", "name": "Ada"})
    services = SimpleNamespace(
        record=SimpleNamespace(create_record=AsyncMock(return_value=record)),
        table=SimpleNamespace(get_table=AsyncMock()),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)
    monkeypatch.setattr(
        pod_adapter, "_table_context", AsyncMock(return_value=SimpleNamespace())
    )

    request = PodWriteRecordRequest(
        action="create",
        table_name="t",
        data='{"name": "Ada", "age": 36, "tags": ["x"]}',
    )
    # The validator normalizes the JSON string to a dict before the tool runs.
    assert request.data == {"name": "Ada", "age": 36, "tags": ["x"]}

    result = await pod_adapter.pod_write_record(_run_ctx(), request)
    assert result["success"] is True
    # The datastore receives a decoded dict (positional arg), never the raw string.
    written = services.record.create_record.await_args.args[1]
    assert written == {"name": "Ada", "age": 36, "tags": ["x"]}


def test_pod_write_record_data_string_validation():
    from pydantic import ValidationError

    # A blank string is treated as no data (caught later by the non-empty guard).
    blank = PodWriteRecordRequest(action="create", table_name="t", data="   ")
    assert blank.data is None

    # A string that is not valid JSON, or that decodes to a non-object, is a
    # validation error — not a silently-dropped or blank write.
    for bad in ("not json", "[1, 2, 3]", '"just a string"', "42"):
        with pytest.raises(ValidationError):
            PodWriteRecordRequest(action="create", table_name="t", data=bad)


@pytest.mark.asyncio
async def test_pod_get_records_single_vs_list(monkeypatch):
    record = SimpleNamespace(data={"id": "1", "name": "Ada"})
    services = SimpleNamespace(
        table=SimpleNamespace(
            get_table=AsyncMock(return_value=SimpleNamespace()),
            schema_manager=SimpleNamespace(get_schema_name=lambda _pid: "s"),
        ),
        record=SimpleNamespace(
            get_record=AsyncMock(return_value=record),
            list_records=AsyncMock(return_value=([record], 1)),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)
    monkeypatch.setattr(
        pod_adapter, "_table_context", AsyncMock(return_value=SimpleNamespace())
    )

    single = await pod_adapter.pod_get_records(
        _run_ctx(), PodGetRecordsRequest(table_name="customers", record_id="1")
    )
    assert single["record"]["name"] == "Ada"
    services.record.get_record.assert_awaited_once()

    listed = await pod_adapter.pod_get_records(
        _run_ctx(), PodGetRecordsRequest(table_name="customers")
    )
    assert listed["total"] == 1
    services.record.list_records.assert_awaited_once()


@pytest.mark.asyncio
async def test_pod_read_file_markdown_returns_page_range(monkeypatch):
    entity = SimpleNamespace(
        path="/pod/report.pdf", mime_type="application/pdf", size_bytes=10
    )
    services = SimpleNamespace(
        file=SimpleNamespace(
            download_file_content_by_path=AsyncMock(
                return_value=(entity, b"%PDF-1.4\x00\xff")
            ),
            get_document_markdown=AsyncMock(return_value=(entity, "## Page 2", 5)),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_read_file(
        _run_ctx(),
        PodReadFileRequest(path="/pod/report.pdf", page_start=2, page_end=2),
    )

    assert result["success"] is True
    assert result["format"] == "markdown"
    assert result["page_count"] == 5
    assert result["markdown"] == "## Page 2"
    services.file.get_document_markdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_pod_read_file_text_decodes_utf8(monkeypatch):
    entity = SimpleNamespace(
        path="/pod/notes.txt", mime_type="text/plain", size_bytes=5
    )
    services = SimpleNamespace(
        file=SimpleNamespace(
            download_file_content_by_path=AsyncMock(return_value=(entity, b"hello"))
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_read_file(
        _run_ctx(), PodReadFileRequest(path="/pod/notes.txt")
    )
    assert result["success"] is True
    assert result["format"] == "text"
    assert result["text"] == "hello"


@pytest.mark.asyncio
async def test_a_text_file_is_returned_as_its_own_original_content(monkeypatch):
    """A file with text of its own is that text -- never a rendering of it."""
    entity = SimpleNamespace(
        path="/me/notes.md", mime_type="text/markdown", size_bytes=11
    )
    markdown = AsyncMock()
    services = SimpleNamespace(
        file=SimpleNamespace(
            download_file_content_by_path=AsyncMock(
                return_value=(entity, b"# hello\nyo")
            ),
            get_document_markdown=markdown,
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_read_file(
        _run_ctx(), PodReadFileRequest(path="/me/notes.md")
    )

    assert result["format"] == "text"
    assert result["text"] == "# hello\nyo"
    markdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_html_is_returned_as_html_not_as_prose(monkeypatch):
    """HTML has text of its own, so converting it would discard the markup.

    This is the case that decides the rule: an earlier version routed by file
    type and would have handed back a rendering with the tags thrown away.
    """
    entity = SimpleNamespace(path="/me/page.html", mime_type="text/html", size_bytes=40)
    markdown = AsyncMock()
    services = SimpleNamespace(
        file=SimpleNamespace(
            download_file_content_by_path=AsyncMock(
                return_value=(entity, b"<h1>Title</h1><p>body</p>")
            ),
            get_document_markdown=markdown,
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_read_file(
        _run_ctx(), PodReadFileRequest(path="/me/page.html")
    )

    assert result["format"] == "text"
    assert result["text"] == "<h1>Title</h1><p>body</p>"
    markdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_pdf_is_read_as_its_converted_text(monkeypatch):
    """A PDF has no text of its own, so the conversion is the answer.

    It used to need `format='markdown'`; without it a PDF came back as
    `binary: true` and an instruction to call again -- a round trip that
    answers itself, and one the tool sweep's agent found by failing first.
    """
    entity = SimpleNamespace(
        path="/me/toolcheck/toolcheck.pdf",
        mime_type="application/pdf",
        size_bytes=2138,
    )
    services = SimpleNamespace(
        file=SimpleNamespace(
            download_file_content_by_path=AsyncMock(
                return_value=(entity, b"%PDF-1.4\x00\xff binary")
            ),
            get_document_markdown=AsyncMock(
                return_value=(entity, "# Toolcheck\n\nbody", 2)
            ),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_read_file(
        _run_ctx(), PodReadFileRequest(path="/me/toolcheck/toolcheck.pdf")
    )

    assert result["format"] == "markdown"
    assert result["converted"] is True
    assert result["markdown"] == "# Toolcheck\n\nbody"
    assert result["page_count"] == 2
    assert "binary" not in result


@pytest.mark.asyncio
async def test_a_binary_with_no_conversion_says_which_kind_of_missing(monkeypatch):
    """A PNG will never have text; a PENDING PDF does not have it yet.

    Both used to be one flat "Binary file" hint. The reader's message already
    tells them apart, so carry it through rather than restate it worse.
    """
    from app.modules.datastore.contracts import DatastoreFileNotFoundError

    entity = SimpleNamespace(path="/me/photo.png", mime_type="image/png", size_bytes=90)
    services = SimpleNamespace(
        file=SimpleNamespace(
            download_file_content_by_path=AsyncMock(
                return_value=(entity, b"\x89PNG\r\n\x1a\n")
            ),
            get_document_markdown=AsyncMock(
                side_effect=DatastoreFileNotFoundError(
                    "/me/photo.png is not an indexable document, so no markdown "
                    "was derived from it."
                )
            ),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_read_file(
        _run_ctx(), PodReadFileRequest(path="/me/photo.png")
    )

    assert result["binary"] is True
    assert "not an indexable document" in result["hint"]


@pytest.mark.asyncio
async def test_empty_search_says_when_files_are_still_being_processed(monkeypatch):
    """An empty list is two different answers, and only one of them is an answer.

    A pod whose files have not been indexed searches cleanly and returns [] --
    identical to a pod that genuinely holds nothing on the topic. An agent
    cannot tell them apart, so it reports the second with confidence when the
    truth is the first. This is the whole reason the sweep's `pod_search_files`
    finding was rated more dangerous than the outright failures beside it.
    """
    services = SimpleNamespace(
        file=SimpleNamespace(
            search_files=AsyncMock(return_value=[]),
            count_files_missing_from_the_index=AsyncMock(return_value=(3, 0)),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_search_files(
        _run_ctx(), SearchFilesRequest(query="quokka telemetry handshake")
    )

    assert result["success"] is True
    assert result["results"] == []
    assert result["files_awaiting_processing"] == 3
    assert "still being processed" in result["note"]


@pytest.mark.asyncio
async def test_an_empty_search_on_a_fully_indexed_pod_stays_a_plain_answer(monkeypatch):
    """No caveat when there is nothing to caveat -- that would be noise on every
    genuine miss, and would teach a reader to ignore the field that matters.

    "Fully indexed" here means both counts are zero. It used to mean only the
    queued one, which is why a pod whose every file had FAILED read as healthy.
    """
    services = SimpleNamespace(
        file=SimpleNamespace(
            search_files=AsyncMock(return_value=[]),
            count_files_missing_from_the_index=AsyncMock(return_value=(0, 0)),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_search_files(
        _run_ctx(), SearchFilesRequest(query="nothing here")
    )

    assert result["results"] == []
    assert "files_awaiting_processing" not in result
    assert "note" not in result


@pytest.mark.asyncio
async def test_a_search_with_hits_never_pays_for_the_pending_count(monkeypatch):
    """The count is a query. A search that found something has already answered
    the question, so it must not run."""
    counter = AsyncMock(return_value=99)
    services = SimpleNamespace(
        file=SimpleNamespace(
            search_files=AsyncMock(return_value=[{"path": "/me/a.md"}]),
            count_files_missing_from_the_index=counter,
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_search_files(
        _run_ctx(), SearchFilesRequest(query="a")
    )

    assert result["results"]
    assert "files_awaiting_processing" not in result
    counter.assert_not_awaited()


@pytest.mark.asyncio
async def test_pod_view_document_pages_returns_images_and_url_refs(monkeypatch):
    from app.modules.datastore.services.files.renderer import RenderedPage
    from pydantic_ai import BinaryContent, ToolReturn

    entity = SimpleNamespace(path="/pod/report.pdf", pod_id=uuid4())
    pages = [
        RenderedPage(1, b"jpeg-1", False, "pods/x/rendered/report.pdf/page_0001.jpg"),
        RenderedPage(2, b"jpeg-2", True, "pods/x/rendered/report.pdf/page_0002.jpg"),
    ]
    services = SimpleNamespace(
        file=SimpleNamespace(
            render_document_page_images=AsyncMock(return_value=(entity, pages)),
            storage=object(),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    async def fake_build_object_url(storage, key, expires_seconds=None):
        return f"https://signed/{key}", None

    monkeypatch.setattr(pod_adapter, "build_object_url", fake_build_object_url)

    result = await pod_adapter.pod_view_document_pages(
        _run_ctx(),
        ViewDocumentPagesRequest(path="/pod/report.pdf", page_start=1, page_end=2),
    )

    assert isinstance(result, ToolReturn)
    # Model receives the bytes inline.
    assert [c.data for c in result.content] == [b"jpeg-1", b"jpeg-2"]
    assert all(isinstance(c, BinaryContent) for c in result.content)
    # DB only persists URL references, never bytes.
    refs = result.return_value["pages"]
    assert [r["page_number"] for r in refs] == [1, 2]
    assert all(r["url"].startswith("https://signed/") for r in refs)
    assert result.return_value["rendered_pages"] == [1]
    assert result.return_value["cached_pages"] == [2]
    dumped = str(result.return_value)
    assert "jpeg-1" not in dumped and "jpeg-2" not in dumped


@pytest.mark.asyncio
async def test_pod_view_document_pages_non_pdf_returns_friendly_error(monkeypatch):
    @asynccontextmanager
    async def denying(deps):  # noqa: ANN001
        del deps
        raise DomainError(
            "Visual page rendering is only supported for PDFs; use markdown.",
            code="VALIDATION_ERROR",
            status_code=400,
        )
        yield  # pragma: no cover

    monkeypatch.setattr(pod_adapter, "pod_services", denying)

    result = await pod_adapter.pod_view_document_pages(
        _run_ctx(),
        ViewDocumentPagesRequest(path="/pod/notes.docx", page_start=1),
    )

    assert result["success"] is False
    assert "PDF" in result["error"]
    assert result.get("needs_approval") is None


@pytest.mark.asyncio
async def test_pod_get_file_url_returns_url(monkeypatch):
    from datetime import datetime, timezone

    entity = SimpleNamespace(path="/pod/report.pdf")
    expires = datetime(2026, 1, 1, tzinfo=timezone.utc)
    services = SimpleNamespace(
        file=SimpleNamespace(
            get_file_url=AsyncMock(
                return_value=(entity, "https://signed/report.pdf", expires)
            )
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_get_file_url(
        _run_ctx(), GetFileUrlRequest(path="/pod/report.pdf")
    )

    assert result["success"] is True
    assert result["url_type"] == "app"
    assert result["url"] == "https://signed/report.pdf"
    assert result["app_url"].endswith("/files?file=/pod/report.pdf")
    assert result["expires_at"] == expires.isoformat()


@pytest.mark.asyncio
async def test_pod_get_file_url_public_mints_signed_url(monkeypatch):
    from datetime import datetime, timezone

    entity = SimpleNamespace(path="/pod/report.pdf")
    expires = datetime(2026, 1, 1, tzinfo=timezone.utc)
    create_signed_url = AsyncMock(
        return_value=(entity, "https://api/s/abc123", expires, 5)
    )
    services = SimpleNamespace(
        file=SimpleNamespace(create_signed_url=create_signed_url),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_get_file_url(
        _run_ctx(),
        GetFileUrlRequest(
            path="/pod/report.pdf", url_type="public", expires_seconds=3600, max_hits=5
        ),
    )

    assert result["success"] is True
    assert result["url_type"] == "public"
    assert result["signed_url"] == "https://api/s/abc123"
    assert result["max_hits"] == 5
    assert result["expires_at"] == expires.isoformat()
    create_signed_url.assert_awaited_once()
    _entity_args, kwargs = create_signed_url.call_args
    assert kwargs == {"expires_seconds": 3600, "max_hits": 5}


@pytest.mark.asyncio
async def test_pod_write_file_creates_new_file(monkeypatch):
    entity = SimpleNamespace(path="/me/report.md", size_bytes=5)
    services = SimpleNamespace(
        file=SimpleNamespace(create_file=AsyncMock(return_value=entity)),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_write_file(
        _run_ctx(), PodWriteFileRequest(path="/me/report.md", content="hello")
    )

    assert result == {
        "success": True,
        "path": "/me/report.md",
        "size_bytes": 5,
        "created": True,
    }
    services.file.create_file.assert_awaited_once()
    args, kwargs = services.file.create_file.call_args
    assert args[1] == "report.md"
    assert args[2] == b"hello"
    assert kwargs["directory_path"] == "/me"


@pytest.mark.asyncio
async def test_pod_write_file_relative_path_resolves_against_pod_cwd(monkeypatch):
    entity = SimpleNamespace(path="/me/c/2026-07-02/ab3f2k7q/notes.md", size_bytes=3)
    services = SimpleNamespace(
        file=SimpleNamespace(create_file=AsyncMock(return_value=entity)),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    ctx = _run_ctx(pod_cwd="/me/c/2026-07-02/ab3f2k7q")
    result = await pod_adapter.pod_write_file(
        ctx, PodWriteFileRequest(path="notes.md", content="hey")
    )

    assert result["success"] is True
    args, kwargs = services.file.create_file.call_args
    assert args[1] == "notes.md"
    assert kwargs["directory_path"] == "/me/c/2026-07-02/ab3f2k7q"


@pytest.mark.asyncio
async def test_pod_write_file_conflict_without_overwrite_returns_error(monkeypatch):
    services = SimpleNamespace(
        file=SimpleNamespace(
            create_file=AsyncMock(side_effect=DatastoreConflictError("exists")),
            resolve_update_file=AsyncMock(),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_write_file(
        _run_ctx(),
        PodWriteFileRequest(path="/me/report.md", content="hello", overwrite=False),
    )

    assert result["success"] is False
    assert "already exists" in result["error"]
    services.file.resolve_update_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_pod_write_file_conflict_with_overwrite_updates_existing(monkeypatch):
    plan = object()
    updated = SimpleNamespace(path="/me/report.md", size_bytes=7)
    services = SimpleNamespace(
        file=SimpleNamespace(
            create_file=AsyncMock(side_effect=DatastoreConflictError("exists")),
            resolve_update_file=AsyncMock(return_value=plan),
            write_update_storage=AsyncMock(),
            persist_update_file=AsyncMock(return_value=updated),
            finalize_update_file=AsyncMock(),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_write_file(
        _run_ctx(),
        PodWriteFileRequest(path="/me/report.md", content="hello", overwrite=True),
    )

    assert result == {
        "success": True,
        "path": "/me/report.md",
        "size_bytes": 7,
        "created": False,
    }
    services.file.resolve_update_file.assert_awaited_once()
    services.file.write_update_storage.assert_awaited_once_with(plan, ANY)
    services.file.persist_update_file.assert_awaited_once_with(plan)
    services.file.finalize_update_file.assert_awaited_once_with(plan, updated)


@pytest.mark.asyncio
async def test_a_pod_whose_files_all_failed_does_not_search_clean(monkeypatch):
    """The state the sweep actually hit, and the one the first signal missed.

    The queued count covers files that will become searchable by waiting. Once
    they have all failed it is zero, so the caveat never fired and an empty
    result was indistinguishable from a healthy pod holding nothing on the
    subject -- while every document in it was unreadable.
    """
    services = SimpleNamespace(
        file=SimpleNamespace(
            search_files=AsyncMock(return_value=[]),
            count_files_missing_from_the_index=AsyncMock(return_value=(0, 2)),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_search_files(
        _run_ctx(), SearchFilesRequest(query="quokka telemetry handshake")
    )

    assert result["success"] is True
    assert result["files_failed_processing"] == 2
    assert "could not be processed" in result["note"]
    # The advice for a queued file is wrong for a failed one: waiting fixes
    # nothing, and telling an agent to retry sends it round a loop with no end.
    assert "retry once processing finishes" not in result["note"]


@pytest.mark.asyncio
async def test_queued_and_failed_files_are_reported_as_the_different_things(
    monkeypatch,
):
    """A pod can be mid-backfill and holding broken files at the same time."""
    services = SimpleNamespace(
        file=SimpleNamespace(
            search_files=AsyncMock(return_value=[]),
            count_files_missing_from_the_index=AsyncMock(return_value=(4, 1)),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_search_files(
        _run_ctx(), SearchFilesRequest(query="anything")
    )

    assert result["files_awaiting_processing"] == 4
    assert result["files_failed_processing"] == 1
    assert "still being processed" in result["note"]
    assert "could not be processed" in result["note"]
