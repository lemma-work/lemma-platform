from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from app.modules.datastore.infrastructure import kreuzberg_helper as kreuzberg_module
from app.modules.datastore.infrastructure.kreuzberg_circuit import (
    reset_kreuzberg_circuit,
)
from app.modules.datastore.infrastructure.kreuzberg_helper import (
    KreuzbergCompatibilityError,
    KreuzbergExtractionResult,
    KreuzbergHelper,
    KreuzbergTransientError,
)


@pytest.fixture(autouse=True)
def _reset_circuit():
    """The Kreuzberg circuit breaker is a process-wide singleton; reset it around
    every test so failures recorded by one test can't open it for the next."""
    reset_kreuzberg_circuit()
    yield
    reset_kreuzberg_circuit()


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _PostContext:
    """A fake ``session.post(...)`` context manager whose first ``fail_times``
    entries raise a transient connection error, then yields ``response``."""

    def __init__(self, owner: "_FlakyPost"):
        self._owner = owner

    async def __aenter__(self):
        self._owner.attempts += 1
        if self._owner.attempts <= self._owner.fail_times:
            raise aiohttp.ClientConnectionError("connection refused")
        return self._owner.response

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FlakyPost:
    def __init__(self, *, fail_times: int, response):
        self.fail_times = fail_times
        self.response = response
        self.attempts = 0

    def __call__(self, *args, **kwargs):
        return _PostContext(self)


class _AlwaysTimeoutPost:
    def __init__(self):
        self.attempts = 0

    def __call__(self, *args, **kwargs):
        owner = self

        class _Context:
            async def __aenter__(self):
                owner.attempts += 1
                raise TimeoutError("request timed out")

            async def __aexit__(self, exc_type, exc, tb):
                return None

        return _Context()


def _fake_client_session_factory(calls: list[dict]):
    def _factory(**kwargs):
        calls.append(kwargs)
        return _FakeSession()

    return _factory


@pytest.mark.asyncio
async def test_process_file_uses_native_pdf_config_with_150_dpi(monkeypatch):
    """A native (text-layer) PDF is extracted in a single pass with force_ocr off
    and 150-DPI image rendering, but keeps the full layout/table config so the
    standardized rich markdown (headers, text tables, embedded images) holds."""
    helper = KreuzbergHelper()
    helper.base_url = "http://localhost:8002"

    monkeypatch.setattr(helper, "_pdf_needs_ocr", AsyncMock(return_value=False))
    extract_mock = AsyncMock(
        return_value=KreuzbergExtractionResult(
            {"content": "native text", "chunks": None}
        )
    )
    chunk_mock = AsyncMock(return_value=[{"text": "native text", "metadata": {}}])
    session_calls: list[dict] = []

    monkeypatch.setattr(
        "aiohttp.ClientSession", _fake_client_session_factory(session_calls)
    )
    monkeypatch.setattr(helper, "_extract", extract_mock)
    monkeypatch.setattr(helper, "_chunk_content", chunk_mock)

    result = await helper.process_file(b"pdf-bytes", "paper.pdf")

    assert session_calls == [{"timeout": helper.request_timeout}]
    assert extract_mock.await_count == 1
    config = extract_mock.await_args.kwargs["config"]
    assert config["images"] == {"extract_images": True, "target_dpi": 150}
    assert "force_ocr" not in config
    # Layout/table config is kept on the native path (preserves text tables).
    assert config["layout"]["table_model"] == "tatr"
    assert config["pdf_options"]["hierarchy"]["enabled"] is True
    assert config["pdf_options"]["allow_single_column_tables"] is True
    assert result.extraction_mode == "direct"
    assert result.get_chunks() == [{"text": "native text", "metadata": {}}]


@pytest.mark.asyncio
async def test_process_file_uses_heavy_pdf_config_for_scanned_pdf(monkeypatch):
    """A scanned PDF (no text layer, detected up front) gets the full heavy
    config — 300 DPI, RT-DETR layout + TATR table model + hierarchy + force_ocr
    — in a single pass (no reactive re-extraction). Requires OCR opt-in."""
    helper = KreuzbergHelper()
    helper.base_url = "http://localhost:8002"

    monkeypatch.setattr(
        kreuzberg_module.datastore_settings, "document_processing_ocr_enabled", True
    )
    monkeypatch.setattr(helper, "_pdf_needs_ocr", AsyncMock(return_value=True))
    extract_mock = AsyncMock(
        return_value=KreuzbergExtractionResult({"content": "ocr text", "chunks": None})
    )
    chunk_mock = AsyncMock(return_value=[{"text": "ocr text", "metadata": {}}])
    session_calls: list[dict] = []

    monkeypatch.setattr(
        "aiohttp.ClientSession", _fake_client_session_factory(session_calls)
    )
    monkeypatch.setattr(helper, "_extract", extract_mock)
    monkeypatch.setattr(helper, "_chunk_content", chunk_mock)

    result = await helper.process_file(b"pdf-bytes", "scan.pdf")

    assert extract_mock.await_count == 1
    config = extract_mock.await_args.kwargs["config"]
    assert config["force_ocr"] is True
    assert config["images"] == {"extract_images": True, "target_dpi": 300}
    assert config["pdf_options"]["hierarchy"]["enabled"] is True
    assert config["layout"]["table_model"] == "tatr"
    assert result.extraction_mode == "ocr"
    assert result.get_chunks() == [{"text": "ocr text", "metadata": {}}]


@pytest.mark.asyncio
async def test_process_file_ocr_disabled_by_default_skips_probe_and_uses_native(
    monkeypatch,
):
    """With OCR off (the default), even a PDF that a probe WOULD call scanned is
    extracted with the fast native config, and the probe is never invoked."""
    helper = KreuzbergHelper()
    helper.base_url = "http://localhost:8002"

    # If this were consulted it would classify scanned — assert it is NOT.
    probe = AsyncMock(return_value=True)
    monkeypatch.setattr(helper, "_pdf_needs_ocr", probe)
    extract_mock = AsyncMock(
        return_value=KreuzbergExtractionResult(
            {"content": "native text", "chunks": None}
        )
    )
    monkeypatch.setattr("aiohttp.ClientSession", _fake_client_session_factory([]))
    monkeypatch.setattr(helper, "_extract", extract_mock)
    monkeypatch.setattr(helper, "_chunk_content", AsyncMock(return_value=[]))

    result = await helper.process_file(b"pdf-bytes", "scan.pdf")

    probe.assert_not_awaited()
    assert extract_mock.await_count == 1
    config = extract_mock.await_args.kwargs["config"]
    assert "force_ocr" not in config
    assert config["images"]["target_dpi"] == 150
    assert result.extraction_mode == "direct"


@pytest.mark.asyncio
async def test_process_file_ocr_disabled_does_not_retry_on_empty_extract(monkeypatch):
    """With OCR off, an empty native extraction is NOT re-run with forced OCR —
    the scanned doc simply degrades to its (empty) text layer."""
    helper = KreuzbergHelper()
    helper.base_url = "http://localhost:8002"

    extract_mock = AsyncMock(
        return_value=KreuzbergExtractionResult({"content": "", "chunks": None})
    )
    monkeypatch.setattr("aiohttp.ClientSession", _fake_client_session_factory([]))
    monkeypatch.setattr(helper, "_extract", extract_mock)
    monkeypatch.setattr(helper, "_chunk_content", AsyncMock(return_value=[]))

    result = await helper.process_file(b"pdf-bytes", "scan.pdf")

    assert extract_mock.await_count == 1  # no forced-OCR retry
    assert result.extraction_mode == "direct"


def test_build_extract_config_native_pdf_uses_150_dpi_keeps_layout():
    helper = KreuzbergHelper()
    config = helper._build_extract_config("application/pdf", force_ocr=False)

    # Native: 150-DPI images, no forced OCR — but the layout/table config stays
    # (lighter "fast" layout preset) so text tables are still reconstructed.
    assert config["images"]["target_dpi"] == 150
    assert "force_ocr" not in config
    assert config["layout"]["preset"] == "fast"
    assert config["layout"]["table_model"] == "tatr"
    assert config["pdf_options"]["hierarchy"]["enabled"] is True
    assert config["pdf_options"]["allow_single_column_tables"] is True


def test_build_extract_config_can_disable_model_backed_layout(monkeypatch):
    monkeypatch.setattr(
        kreuzberg_module.datastore_settings,
        "document_processing_layout_enabled",
        False,
    )

    config = KreuzbergHelper()._build_extract_config("application/pdf", force_ocr=False)

    assert "layout" not in config
    assert "hierarchy" not in config["pdf_options"]
    assert config["pdf_options"]["extract_images"] is True


def test_build_extract_config_scanned_pdf_forces_ocr_at_300_dpi():
    helper = KreuzbergHelper()
    config = helper._build_extract_config("application/pdf", force_ocr=True)

    # Scanned: same structure config, but force OCR and 300-DPI images.
    assert config["images"]["target_dpi"] == 300
    assert config["force_ocr"] is True
    assert config["layout"]["table_model"] == "tatr"
    assert config["pdf_options"]["hierarchy"]["enabled"] is True


@pytest.mark.asyncio
async def test_pdf_needs_ocr_true_for_low_text_density(monkeypatch):
    helper = KreuzbergHelper()
    # 5 pages, 50 chars total → avg 10 < 100 threshold → scanned.
    monkeypatch.setattr(
        kreuzberg_module, "get_pdf_text_sample", lambda *a, **k: (5, 50)
    )
    assert await helper._pdf_needs_ocr(b"pdf-bytes") is True


@pytest.mark.asyncio
async def test_pdf_needs_ocr_false_for_high_text_density(monkeypatch):
    helper = KreuzbergHelper()
    # 5 pages, 5000 chars total → avg 1000 ≥ 100 → native.
    monkeypatch.setattr(
        kreuzberg_module, "get_pdf_text_sample", lambda *a, **k: (5, 5000)
    )
    assert await helper._pdf_needs_ocr(b"pdf-bytes") is False


@pytest.mark.asyncio
async def test_pdf_needs_ocr_false_when_probe_raises(monkeypatch):
    helper = KreuzbergHelper()

    def _boom(*a, **k):
        raise RuntimeError("encrypted or corrupt")

    monkeypatch.setattr(kreuzberg_module, "get_pdf_text_sample", _boom)
    # A probe failure must NOT fail the extraction — fall back to the native path.
    assert await helper._pdf_needs_ocr(b"pdf-bytes") is False


@pytest.mark.asyncio
async def test_pdf_needs_ocr_false_for_garbage_bytes():
    # Integration: real pypdfium2 on non-PDF bytes raises → safe default False.
    helper = KreuzbergHelper()
    assert await helper._pdf_needs_ocr(b"not a pdf at all") is False


@pytest.mark.asyncio
async def test_process_file_retries_with_forced_ocr_when_initial_pdf_extract_is_empty(
    monkeypatch,
):
    helper = KreuzbergHelper()
    helper.base_url = "http://localhost:8002"

    # Classified native up front, but the first pass extracts no text at all →
    # the safety-net retry forces OCR (and the heavy config). Requires OCR opt-in.
    monkeypatch.setattr(
        kreuzberg_module.datastore_settings, "document_processing_ocr_enabled", True
    )
    monkeypatch.setattr(helper, "_pdf_needs_ocr", AsyncMock(return_value=False))
    extract_mock = AsyncMock(
        side_effect=[
            KreuzbergExtractionResult({"content": "", "chunks": None}),
            KreuzbergExtractionResult({"content": "ocr text", "chunks": None}),
        ]
    )
    chunk_mock = AsyncMock(return_value=[{"text": "ocr text", "metadata": {}}])
    session_calls: list[dict] = []

    monkeypatch.setattr(
        "aiohttp.ClientSession", _fake_client_session_factory(session_calls)
    )
    monkeypatch.setattr(helper, "_extract", extract_mock)
    monkeypatch.setattr(helper, "_chunk_content", chunk_mock)

    result = await helper.process_file(b"pdf-bytes", "scan.pdf")

    assert session_calls == [{"timeout": helper.request_timeout}]
    assert extract_mock.await_count == 2
    first_config = extract_mock.await_args_list[0].kwargs["config"]
    assert first_config["include_document_structure"] is True
    assert "force_ocr" not in first_config  # native first pass: no forced OCR
    assert first_config["images"]["target_dpi"] == 150  # native DPI
    assert extract_mock.await_args_list[1].kwargs["config"]["force_ocr"] is True
    assert result.extraction_mode == "ocr"
    assert result.get_chunks() == [{"text": "ocr text", "metadata": {}}]


@pytest.mark.asyncio
async def test_process_file_falls_back_to_full_content_when_chunking_returns_nothing(
    monkeypatch,
):
    helper = KreuzbergHelper()
    helper.base_url = "http://localhost:8002"

    extract_mock = AsyncMock(
        return_value=KreuzbergExtractionResult({"content": "plain extracted text"})
    )
    chunk_mock = AsyncMock(return_value=[])
    session_calls: list[dict] = []

    monkeypatch.setattr(
        "aiohttp.ClientSession", _fake_client_session_factory(session_calls)
    )
    monkeypatch.setattr(helper, "_extract", extract_mock)
    monkeypatch.setattr(helper, "_chunk_content", chunk_mock)

    result = await helper.process_file(b"text", "notes.txt")

    assert session_calls == [{"timeout": helper.request_timeout}]
    assert extract_mock.await_count == 1
    assert result.get_chunks() == [{"text": "plain extracted text", "metadata": {}}]


def test_get_images_normalizes_snake_case_kreuzberg_payload():
    result = KreuzbergExtractionResult(
        {
            "images": [
                {
                    "image_index": 2,
                    "format": "png",
                    "data": [137, 80, 78, 71],
                }
            ]
        }
    )

    assert result.get_images() == [
        {
            "name": "image_2.png",
            "content": b"\x89PNG",
            "mime_type": "image/png",
            "page_number": None,
        }
    ]


def test_get_pages_normalizes_page_content_and_images():
    result = KreuzbergExtractionResult(
        {
            "pages": [
                {
                    "page_number": 3,
                    "content": "Page text",
                    "images": [
                        {
                            "format": "png",
                            "data": [137, 80, 78, 71],
                        }
                    ],
                }
            ]
        }
    )

    assert result.get_pages() == [
        {
            "page_number": 3,
            "content": "Page text",
            "tables": [],
            "images": [
                {
                    "name": "page_0003_image_0.png",
                    "content": b"\x89PNG",
                    "mime_type": "image/png",
                    "page_number": 3,
                }
            ],
            "is_blank": None,
        }
    ]


@pytest.mark.asyncio
async def test_extract_retries_transient_connection_error_then_succeeds(monkeypatch):
    """A transient connection refusal is retried with backoff and the extraction
    succeeds once the service is reachable again."""
    sleeps: list[float] = []

    async def _fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(kreuzberg_module.asyncio, "sleep", _fake_sleep)

    response = SimpleNamespace(
        status=200,
        json=AsyncMock(return_value=[{"content": "ok", "chunks": [{"text": "ok"}]}]),
    )
    session = SimpleNamespace(post=_FlakyPost(fail_times=2, response=response))

    helper = KreuzbergHelper()
    result = await helper._extract(
        session,
        file_content=b"bytes",
        filename="paper.pdf",
        mime_type="application/pdf",
        config=None,
    )

    assert result.content == "ok"
    # Two failed attempts → two backoff sleeps before the third attempt wins.
    assert len(sleeps) == 2
    assert session.post.attempts == 3


@pytest.mark.asyncio
async def test_extract_does_not_immediately_retry_timeout():
    post = _AlwaysTimeoutPost()
    helper = KreuzbergHelper()

    with pytest.raises(KreuzbergTransientError, match="timed out"):
        await helper._extract(
            SimpleNamespace(post=post),
            file_content=b"bytes",
            filename="book.pdf",
            mime_type="application/pdf",
        )

    assert post.attempts == 1


@pytest.mark.asyncio
async def test_config_fallback_only_handles_schema_compatibility(monkeypatch):
    helper = KreuzbergHelper()
    extraction = KreuzbergExtractionResult({"content": "ok"})
    extract = AsyncMock(
        side_effect=[KreuzbergCompatibilityError("unsupported field"), extraction]
    )
    monkeypatch.setattr(helper, "_extract", extract)

    result = await helper._extract_with_config_fallback(
        SimpleNamespace(),
        file_content=b"bytes",
        filename="book.pdf",
        mime_type="application/pdf",
        config=helper._build_extract_config("application/pdf", force_ocr=False),
    )

    assert result is extraction
    assert extract.await_count == 2


@pytest.mark.asyncio
async def test_config_fallback_does_not_multiply_transient_failure(monkeypatch):
    helper = KreuzbergHelper()
    extract = AsyncMock(side_effect=KreuzbergTransientError("service busy"))
    monkeypatch.setattr(helper, "_extract", extract)

    with pytest.raises(KreuzbergTransientError, match="service busy"):
        await helper._extract_with_config_fallback(
            SimpleNamespace(),
            file_content=b"bytes",
            filename="book.pdf",
            mime_type="application/pdf",
            config=helper._build_extract_config("application/pdf", force_ocr=False),
        )

    assert extract.await_count == 1


@pytest.mark.asyncio
async def test_http_503_is_transient_but_not_same_request_retried():
    helper = KreuzbergHelper()
    response = SimpleNamespace(
        status=503,
        text=AsyncMock(return_value="busy"),
    )

    with pytest.raises(KreuzbergTransientError, match="status 503"):
        await helper._raise_for_status(response)


@pytest.mark.asyncio
async def test_extract_streams_content_path_instead_of_buffering(monkeypatch, tmp_path):
    """When given a content_path, _extract streams the file from disk (opens it)
    rather than holding a BytesIO copy of the bytes."""
    doc = tmp_path / "paper.pdf"
    doc.write_bytes(b"PDF-ON-DISK")

    opened: list[str] = []
    real_open = kreuzberg_module.open_binary

    def _spy_open(path):
        opened.append(path)
        return real_open(path)

    monkeypatch.setattr(kreuzberg_module, "open_binary", _spy_open)

    response = SimpleNamespace(
        status=200,
        json=AsyncMock(return_value=[{"content": "ok", "chunks": [{"text": "ok"}]}]),
    )
    session = SimpleNamespace(post=_FlakyPost(fail_times=0, response=response))

    helper = KreuzbergHelper()
    result = await helper._extract(
        session,
        file_content=None,
        filename="paper.pdf",
        mime_type="application/pdf",
        config=None,
        content_path=str(doc),
    )

    assert result.content == "ok"
    # The on-disk source was streamed (opened) rather than buffered from bytes.
    assert opened == [str(doc)]


@pytest.mark.asyncio
async def test_extract_raises_after_exhausting_transient_retries(monkeypatch):
    """Persistent connection failures are retried a bounded number of times and
    then surface as a single RuntimeError rather than retrying forever."""
    sleeps: list[float] = []

    async def _fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(kreuzberg_module.asyncio, "sleep", _fake_sleep)

    session = SimpleNamespace(post=_FlakyPost(fail_times=99, response=None))

    helper = KreuzbergHelper()
    with pytest.raises(KreuzbergTransientError, match="connection failed"):
        await helper._extract(
            session,
            file_content=b"bytes",
            filename="paper.pdf",
            mime_type="application/pdf",
            config=None,
        )

    expected_attempts = (
        kreuzberg_module.datastore_settings.kreuzberg_transient_retry_attempts
    )
    assert session.post.attempts == expected_attempts
    assert len(sleeps) == expected_attempts - 1
