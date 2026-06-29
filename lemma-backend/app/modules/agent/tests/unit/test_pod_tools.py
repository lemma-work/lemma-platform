"""Unit tests for the pod toolset.

These cover the contract that matters to agents: the toolset is registered, a
read tool returns structured pod data, and a write the agent lacks a grant for
comes back as a ``needs_approval`` result (the hand-off to the approval gate)
rather than raising.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod import pydantic_adapter as pod_adapter
from app.modules.agent.tools.pod.models import (
    GetFileUrlRequest,
    PodGetRecordsRequest,
    PodReadFileRequest,
    PodTablesRequest,
    PodWriteRecordRequest,
    ViewDocumentPagesRequest,
)
from app.modules.agent.tools.registry import resolve_agent_toolsets


def _run_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        deps=BaseAgentContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
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


def test_pod_toolset_exposes_exactly_the_nine_tools():
    names = set(pod_adapter.pod_toolset.tools.keys())
    assert names == {
        "pod_tables",
        "pod_get_records",
        "pod_write_record",
        "pod_query",
        "pod_list_files",
        "pod_read_file",
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
        record=SimpleNamespace(
            update_record=AsyncMock(), delete_record=AsyncMock()
        ),
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
    entity = SimpleNamespace(path="/pod/report.pdf")
    services = SimpleNamespace(
        file=SimpleNamespace(
            get_document_markdown=AsyncMock(return_value=(entity, "## Page 2", 5))
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )
    _patch_services(monkeypatch, services)

    result = await pod_adapter.pod_read_file(
        _run_ctx(),
        PodReadFileRequest(
            path="/pod/report.pdf", format="markdown", page_start=2, page_end=2
        ),
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
            download_file_content_by_path=AsyncMock(
                return_value=(entity, b"hello")
            )
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
