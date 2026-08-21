from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.modules.datastore.infrastructure import docling_processor as docling_module
from app.modules.datastore.infrastructure.docling_processor import (
    _PAGE_BREAK_SENTINEL,
    DoclingDocumentProcessor,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"


class _FakeResp:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeSession:
    """Fake aiohttp session that returns queued responses and records calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def post(self, url, data=None):
        self.calls.append(("POST", url))
        return self._responses.pop(0)

    def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        return self._responses.pop(0)


def test_number_page_markers_rewrites_sentinels():
    raw = f"page one body{_PAGE_BREAK_SENTINEL}page two body{_PAGE_BREAK_SENTINEL}page three"
    out = DoclingDocumentProcessor._number_page_markers(raw)
    assert "<!-- PAGE 1 -->" in out
    assert "<!-- PAGE 2 -->" in out
    assert "<!-- PAGE 3 -->" in out
    assert _PAGE_BREAK_SENTINEL not in out
    assert out.index("<!-- PAGE 1 -->") < out.index("<!-- PAGE 2 -->")


def test_markdown_from_response_reads_document_md_content():
    data = {"document": {"md_content": "# Hello\n\nBody"}, "status": "success"}
    assert DoclingDocumentProcessor._markdown_from_response(data) == "# Hello\n\nBody"


def test_markdown_from_response_rejects_unexpected_shape():
    with pytest.raises(RuntimeError, match="Unexpected response"):
        DoclingDocumentProcessor._markdown_from_response({"nope": 1})


def test_extract_requires_configured_url():
    processor = DoclingDocumentProcessor(base_url="")
    with pytest.raises(ValueError, match="Docling serve URL not configured"):
        import asyncio

        asyncio.run(processor.extract(b"x", "a.pdf", mime_type="application/pdf"))


@pytest.mark.asyncio
async def test_extract_builds_paged_extraction_from_docling_markdown(monkeypatch):
    processor = DoclingDocumentProcessor(base_url="http://docling:5001")
    raw = f"# Intro\n\nfirst page{_PAGE_BREAK_SENTINEL}## Methods\n\nsecond page"
    monkeypatch.setattr(processor, "_convert", AsyncMock(return_value=raw))

    pdf_bytes = (_FIXTURES / "bert.pdf").read_bytes()
    extraction = await processor.extract(
        pdf_bytes, "bert.pdf", mime_type="application/pdf"
    )

    assert extraction.extraction_mode == "docling"
    assert "<!-- PAGE 1 -->" in extraction.markdown
    assert "<!-- PAGE 2 -->" in extraction.markdown
    assert extraction.chunks
    # Chunk page spans come from the reconstructed markers.
    assert extraction.chunks[0].page_start == 1
    # PDF page count populated via pypdfium2 (independent of docling markers).
    assert extraction.page_count > 0


def test_supports_page_rendering_inherited():
    processor = DoclingDocumentProcessor(base_url="http://docling:5001")
    assert processor.supports_page_rendering("application/pdf", "a.pdf") is True
    assert processor.supports_page_rendering("text/plain", "a.txt") is False


# -- async submit -> poll -> result flow -----------------------------------


@pytest.mark.asyncio
async def test_convert_uses_async_submit_poll_result_endpoints(monkeypatch):
    """The adapter submits async, polls until success, then fetches the result —
    hitting the async endpoints in order and parsing md_content."""
    session = _FakeSession(
        [
            _FakeResp(200, {"task_id": "t-123", "task_status": "pending"}),  # submit
            _FakeResp(200, {"task_id": "t-123", "task_status": "started"}),  # poll 1
            _FakeResp(200, {"task_id": "t-123", "task_status": "success"}),  # poll 2
            _FakeResp(200, {"document": {"md_content": "# Result"}}),  # result
        ]
    )
    monkeypatch.setattr(docling_module.aiohttp, "ClientSession", lambda **kw: session)

    processor = DoclingDocumentProcessor(base_url="http://docling:5001")
    md = await processor._convert(b"pdf", "paper.pdf", "application/pdf")

    assert md == "# Result"
    urls = [c[1] for c in session.calls]
    assert urls == [
        "http://docling:5001/v1/convert/file/async",
        "http://docling:5001/v1/status/poll/t-123",
        "http://docling:5001/v1/status/poll/t-123",
        "http://docling:5001/v1/result/t-123",
    ]


@pytest.mark.asyncio
async def test_convert_raises_on_failure_status_without_fetching_result(monkeypatch):
    session = _FakeSession(
        [
            _FakeResp(200, {"task_id": "t-9", "task_status": "pending"}),  # submit
            _FakeResp(200, {"task_id": "t-9", "task_status": "failure"}),  # poll
        ]
    )
    monkeypatch.setattr(docling_module.aiohttp, "ClientSession", lambda **kw: session)

    processor = DoclingDocumentProcessor(base_url="http://docling:5001")
    with pytest.raises(RuntimeError, match="Docling conversion failed"):
        await processor._convert(b"pdf", "bad.pdf", "application/pdf")
    # No result fetch after a failure.
    assert not any("result" in c[1] for c in session.calls)


@pytest.mark.asyncio
async def test_await_completion_times_out(monkeypatch):
    processor = DoclingDocumentProcessor(base_url="http://docling:5001")
    processor._conversion_timeout = 0.0  # deadline is already in the past
    processor._poll = AsyncMock(return_value={"task_status": "started"})

    with pytest.raises(RuntimeError, match="timed out"):
        await processor._await_completion(
            session=None,
            task_id="t-1",
            task={"task_status": "started"},
            filename="big.pdf",
        )


@pytest.mark.asyncio
async def test_submit_does_not_resubmit_on_success(monkeypatch):
    """A single successful submit must not be retried (no queue storm)."""
    session = _FakeSession(
        [
            _FakeResp(200, {"task_id": "t-1", "task_status": "success"}),  # submit
            _FakeResp(200, {"document": {"md_content": "ok"}}),  # result
        ]
    )
    monkeypatch.setattr(docling_module.aiohttp, "ClientSession", lambda **kw: session)

    processor = DoclingDocumentProcessor(base_url="http://docling:5001")
    md = await processor._convert(b"pdf", "quick.pdf", "application/pdf")

    assert md == "ok"
    # Exactly one submit (task already success on first poll-less check), one result.
    assert sum(1 for c in session.calls if c[0] == "POST") == 1
