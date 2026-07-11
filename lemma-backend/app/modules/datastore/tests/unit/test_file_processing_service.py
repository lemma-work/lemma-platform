from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.domain.document_processing import (
    DocumentChunk,
    DocumentExtraction,
    DocumentImage,
    DocumentPage,
)
from app.modules.datastore.domain.errors import (
    DatastoreObjectIntegrityError,
    DatastoreObjectNotFoundError,
)
from app.modules.datastore.services.file_processing_service import (
    DatastoreFileProcessingService,
)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ExecuteResult:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.rowcount or None


class _RecordingUowFactory:
    """Fake UnitOfWorkFactory: each call opens a short UoW with one execute result.

    The service opens exactly one short UoW per DB op (get_model, claim,
    mark_completed/mark_failed/mark_not_required), each issuing a single
    statement. Tracking ``active`` lets tests assert that no DB session is held
    during the external storage/extraction I/O between those ops.
    """

    def __init__(self, results):
        self._results = list(results)
        self.active = 0  # currently-open short UoWs
        self.opened = 0  # total short UoWs opened over the run
        self.sessions: list[AsyncMock] = []

    @asynccontextmanager
    async def __call__(self):
        result = self._results.pop(0) if self._results else _ExecuteResult()
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[result])
        self.sessions.append(session)
        self.active += 1
        self.opened += 1
        try:
            yield SimpleNamespace(session=session)
        finally:
            self.active -= 1


def _make_iter_download(data: bytes):
    async def _iter(*_args, **_kwargs):
        yield data

    return _iter


def _make_iter_download_raising(exc: BaseException):
    async def _iter(*_args, **_kwargs):
        raise exc
        yield  # pragma: no cover — makes this an async generator

    return _iter


def _set_source_bytes(service, data: bytes) -> None:
    """Point the streamed extraction read (iter_download) at these bytes.

    _build_extraction streams the original document via storage.iter_download to a
    temp file and passes the path to the processor as content_path, so extraction
    tests configure iter_download (download_file remains for the user-markdown
    source.md path)."""
    service.storage.iter_download = _make_iter_download(data)


def _build_service(factory: _RecordingUowFactory) -> DatastoreFileProcessingService:
    search_service = AsyncMock()
    service = DatastoreFileProcessingService(
        uuid4(), uow_factory=factory, search_service=search_service
    )
    service.storage = AsyncMock()
    service.storage.iter_download = _make_iter_download(b"")
    service.document_processor = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_process_file_async_writes_child_container_and_indexes_chunks():
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={},
    )

    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    pod_id = service.pod_id

    _set_source_bytes(service, b"pdf-bytes")
    service.document_processor.extract.return_value = DocumentExtraction(
        markdown="<!-- PAGE 1 -->\n\n# OCR Output\n\n![](image_0.png)",
        chunks=[DocumentChunk(text="OCR Output", page_start=1, page_end=1)],
        images=[
            DocumentImage(
                name="image_0.png",
                content=b"png-bytes",
                mime_type="image/png",
                page_number=1,
            )
        ],
        pages=[DocumentPage(page_number=1, is_blank=False)],
        detected_languages=["eng"],
        extraction_mode="ocr",
    )

    await service.process_file_async(file_id, {"source": "test"})

    assert service.storage.upload_file.await_count == 3
    service.document_processor.extract.assert_awaited_once_with(
        None,
        "scan.pdf",
        mime_type="application/pdf",
        content_path=ANY,
    )
    service.search_service.index_file_chunks.assert_awaited_once()
    assert (
        service.search_service.index_file_chunks.await_args.args[2]["source"] == "test"
    )
    # Child artifacts are colocated under the source file's hidden container.
    uploaded_paths = [
        call.args[0] for call in service.storage.upload_file.await_args_list
    ]
    assert uploaded_paths == [
        f"pods/{pod_id}/files/manuals/.scan.pdf/document.md",
        f"pods/{pod_id}/files/manuals/.scan.pdf/image_0.png",
        f"pods/{pod_id}/files/manuals/.scan.pdf/manifest.json",
    ]
    # The service persists the processor's page-marked markdown verbatim.
    assert service.storage.upload_file.await_args_list[0].args[1] == (
        b"<!-- PAGE 1 -->\n\n# OCR Output\n\n![](image_0.png)"
    )
    manifest = json.loads(service.storage.upload_file.await_args_list[-1].args[1])
    assert manifest["page_count"] == 1
    assert manifest["pages"] == [
        {
            "page_number": 1,
            "is_blank": False,
            "image_count": 0,
            "table_count": 0,
        }
    ]
    assert [artifact["kind"] for artifact in manifest["artifacts"]] == [
        "markdown",
        "image",
    ]


@pytest.mark.asyncio
async def test_process_file_async_indexes_user_markdown_without_processor():
    """A file flagged markdown_source=user is indexed from its stored source.md
    — chunked in-process — with NO document-processor call, and source.md is
    re-persisted so it survives the projection rewrite."""
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={"markdown_source": "user"},
    )
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    pod_id = service.pod_id
    user_md = b"<!-- PAGE 1 -->\n\n# User Authored\n\nHand-written body."
    service.storage.download_file.return_value = user_md

    await service.process_file_async(file_id)

    # No extraction, and the only download was the user's source.md.
    service.document_processor.extract.assert_not_awaited()
    service.storage.download_file.assert_awaited_once_with(
        f"pods/{pod_id}/files/manuals/.scan.pdf/source.md"
    )
    uploaded_paths = [c.args[0] for c in service.storage.upload_file.await_args_list]
    assert uploaded_paths == [
        f"pods/{pod_id}/files/manuals/.scan.pdf/document.md",
        f"pods/{pod_id}/files/manuals/.scan.pdf/source.md",
        f"pods/{pod_id}/files/manuals/.scan.pdf/manifest.json",
    ]
    # document.md and re-persisted source.md are the user markdown verbatim.
    assert service.storage.upload_file.await_args_list[0].args[1] == user_md
    assert service.storage.upload_file.await_args_list[1].args[1] == user_md
    manifest = json.loads(service.storage.upload_file.await_args_list[-1].args[1])
    assert manifest["markdown_source"] == "user"
    assert manifest["extraction_mode"] == "user_markdown"
    # Chunks come from the user markdown, carrying its page span.
    indexed_chunks = service.search_service.index_file_chunks.await_args.args[1]
    assert indexed_chunks and indexed_chunks[0]["metadata"]["page_number"] == 1


@pytest.mark.asyncio
async def test_process_file_async_user_markdown_with_companion_images():
    """A BYO markdown doc with companion images stores the images as sibling
    child artifacts, lists them in the manifest, and rewrites the markdown image
    references to their basenames so they resolve via the children endpoint."""
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={
            "markdown_source": "user",
            "markdown_asset_names": ["fig1.png"],
        },
    )
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    pod_id = service.pod_id
    source_md = b"<!-- PAGE 1 -->\n\n# Doc\n\n![diagram](images/fig1.png)"

    async def _download(key):
        if key.endswith("/.scan.pdf/source.md"):
            return source_md
        if key.endswith("/.scan.pdf/fig1.png"):
            return b"PNGDATA"
        raise AssertionError(f"unexpected download {key}")

    service.storage.download_file.side_effect = _download

    await service.process_file_async(file_id)

    service.document_processor.extract.assert_not_awaited()
    uploaded = {
        c.args[0]: c.args[1] for c in service.storage.upload_file.await_args_list
    }
    prefix = f"pods/{pod_id}/files/manuals/.scan.pdf"
    # document.md + re-persisted source.md + the companion image + manifest.
    assert set(uploaded) == {
        f"{prefix}/document.md",
        f"{prefix}/source.md",
        f"{prefix}/fig1.png",
        f"{prefix}/manifest.json",
    }
    assert uploaded[f"{prefix}/fig1.png"] == b"PNGDATA"
    # The markdown reference is rewritten to the sibling basename.
    assert b"![diagram](fig1.png)" in uploaded[f"{prefix}/document.md"]
    manifest = json.loads(uploaded[f"{prefix}/manifest.json"])
    assert manifest["markdown_source"] == "user"
    kinds = [a["kind"] for a in manifest["artifacts"]]
    assert "image" in kinds


@pytest.mark.asyncio
async def test_process_file_async_byo_holds_no_db_session_during_io():
    """The bring-your-own path must also release the DB connection during the
    source.md download + chunking + index (no session held across slow I/O)."""
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={"markdown_source": "user"},
    )
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)

    def _assert_no_open_session():
        assert factory.active == 0, "DB session held during external I/O"

    async def _download(*_a, **_k):
        _assert_no_open_session()
        return b"<!-- PAGE 1 -->\n\n# User MD\n\nBody."

    async def _upload(*_a, **_k):
        _assert_no_open_session()

    async def _index(*_a, **_k):
        _assert_no_open_session()

    service.storage.download_file.side_effect = _download
    service.storage.upload_file.side_effect = _upload
    service.search_service.index_file_chunks.side_effect = _index

    await service.process_file_async(file_id)

    service.document_processor.extract.assert_not_awaited()
    # get_model, claim, mark_completed — all short UoWs, none left open.
    assert factory.opened == 5
    assert factory.active == 0


@pytest.mark.asyncio
async def test_process_file_async_falls_back_to_extraction_when_source_md_missing():
    """markdown_source=user but source.md is gone → fall back to extraction."""
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={"markdown_source": "user"},
    )
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    # source.md 404s (buffered read); the original is then streamed for extraction.
    service.storage.download_file.side_effect = [
        DatastoreObjectNotFoundError("missing"),
    ]
    _set_source_bytes(service, b"pdf-bytes")
    service.document_processor.extract.return_value = DocumentExtraction(
        markdown="extracted text",
        chunks=[DocumentChunk(text="extracted text")],
        extraction_mode="direct",
    )

    await service.process_file_async(file_id)

    service.document_processor.extract.assert_awaited_once()
    manifest = json.loads(service.storage.upload_file.await_args_list[-1].args[1])
    assert manifest["markdown_source"] == "extracted"


@pytest.mark.asyncio
async def test_process_file_async_persists_processor_markdown_verbatim():
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="guide.pdf",
        path="/manuals/guide.pdf",
        mime_type="application/pdf",
        file_metadata={},
    )

    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    _set_source_bytes(service, b"pdf-bytes")
    service.document_processor.extract.return_value = DocumentExtraction(
        markdown="<!-- PAGE 1 -->\n\n# Rich heading\n\n| A | B |\n| - | - |\n| 1 | 2 |",
        chunks=[DocumentChunk(text="Rich heading", page_start=1)],
        images=[],
        pages=[DocumentPage(page_number=1)],
        detected_languages=["eng"],
        extraction_mode="direct",
    )

    await service.process_file_async(file_id)

    assert service.storage.upload_file.await_args_list[0].args[1] == (
        b"<!-- PAGE 1 -->\n\n# Rich heading\n\n| A | B |\n| - | - |\n| 1 | 2 |"
    )


@pytest.mark.asyncio
async def test_process_file_async_surfaces_native_chunk_pages_to_index():
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={},
    )
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    _set_source_bytes(service, b"pdf-bytes")
    service.document_processor.extract.return_value = DocumentExtraction(
        markdown="<!-- PAGE 1 -->\n\nA\n\n<!-- PAGE 2 -->\n\nB",
        chunks=[
            DocumentChunk(text="A", page_start=1, page_end=1),
            DocumentChunk(text="B", page_start=2, page_end=3),
        ],
        pages=[DocumentPage(page_number=1), DocumentPage(page_number=2)],
    )

    await service.process_file_async(file_id)

    indexed_chunks = service.search_service.index_file_chunks.await_args.args[1]
    assert indexed_chunks[0]["metadata"]["page_number"] == 1
    assert indexed_chunks[1]["metadata"]["page_number"] == 2
    assert indexed_chunks[1]["metadata"]["page_end"] == 3


@pytest.mark.asyncio
async def test_process_file_async_indexes_personal_file_when_search_enabled():
    file_id = uuid4()
    owner_user_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        owner_user_id=owner_user_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="private.txt",
        path=f"/{owner_user_id}/private.txt",
        mime_type="text/plain",
        file_metadata={},
    )

    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    pod_id = service.pod_id
    streamed_keys: list[str] = []

    def _iter_download(key, *_a, **_k):
        streamed_keys.append(key)
        return _make_iter_download(b"private-bytes")()

    service.storage.iter_download = _iter_download
    service.document_processor.extract.return_value = DocumentExtraction(
        markdown="private text",
        chunks=[DocumentChunk(text="private text")],
        extraction_mode="direct",
    )

    await service.process_file_async(file_id)

    # The original was streamed (not buffered) from the right storage key.
    assert streamed_keys == [f"pods/{pod_id}/files/{owner_user_id}/private.txt"]
    service.document_processor.extract.assert_awaited_once()
    service.search_service.index_file_chunks.assert_awaited_once()
    service.search_service.remove_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_file_async_uses_latest_file_metadata_over_stale_job_metadata():
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={"source": "latest", "editor": "frontend"},
    )

    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    _set_source_bytes(service, b"pdf-bytes")
    service.document_processor.extract.return_value = DocumentExtraction(
        markdown="<!-- PAGE 1 -->\n\nlatest content",
        chunks=[DocumentChunk(text="latest content", page_start=1)],
        pages=[DocumentPage(page_number=1)],
        extraction_mode="direct",
    )

    await service.process_file_async(file_id, {"source": "stale"})

    assert (
        service.search_service.index_file_chunks.await_args.args[2]["source"]
        == "latest"
    )
    assert (
        service.search_service.index_file_chunks.await_args.args[2]["editor"]
        == "frontend"
    )
    # Three short UoWs: get_model, claim_for_processing, mark_completed.
    assert factory.opened == 5


@pytest.mark.asyncio
async def test_process_file_async_skips_when_status_is_not_pending():
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="COMPLETED",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={},
    )

    factory = _RecordingUowFactory(results=[_ScalarResult(file_model)])
    service = _build_service(factory)

    await service.process_file_async(file_id)

    # Only the get_model UoW was opened; no claim / completion.
    assert factory.opened == 1
    service.storage.download_file.assert_not_awaited()
    service.document_processor.extract.assert_not_awaited()
    service.search_service.index_file_chunks.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_file_async_skips_when_claim_is_lost():
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={},
    )

    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(rowcount=0)]
    )
    service = _build_service(factory)

    await service.process_file_async(file_id)

    # get_model + claim (lost) UoWs only; nothing downstream.
    assert factory.opened == 2
    service.storage.download_file.assert_not_awaited()
    service.document_processor.extract.assert_not_awaited()
    service.search_service.index_file_chunks.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_file_async_scopes_short_uows_and_holds_no_session_during_io():
    """Each DB op runs in its own short UoW and NO session is held during the
    storage/extraction/index I/O (the connection-leak fix)."""
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={},
    )

    # One execute result per short UoW, in order: get_model, claim, mark_completed.
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)

    # Every external-I/O call must observe zero open DB sessions.
    def _assert_no_open_session():
        assert factory.active == 0, "DB session held during external I/O"

    extraction = DocumentExtraction(
        markdown="<!-- PAGE 1 -->\n\n# OCR Output",
        chunks=[DocumentChunk(text="OCR Output", page_start=1, page_end=1)],
        images=[],
        pages=[DocumentPage(page_number=1, is_blank=False)],
        detected_languages=["eng"],
        extraction_mode="ocr",
    )

    async def _iter_download(*_args, **_kwargs):
        _assert_no_open_session()
        yield b"pdf-bytes"

    async def _extract(*_args, **_kwargs):
        _assert_no_open_session()
        return extraction

    async def _upload(*_args, **_kwargs):
        _assert_no_open_session()

    async def _index(*_args, **_kwargs):
        _assert_no_open_session()

    service.storage.iter_download = _iter_download
    service.storage.upload_file.side_effect = _upload
    service.document_processor.extract.side_effect = _extract
    service.search_service.index_file_chunks.side_effect = _index

    await service.process_file_async(file_id, {"source": "test"})

    # Five distinct short UoWs opened (get, claim, two claim checks,
    # completion), all closed and each issuing exactly one statement.
    assert factory.opened == 5
    assert factory.active == 0
    assert [s.execute.await_count for s in factory.sessions] == [1, 1, 0, 0, 1]
    service.search_service.index_file_chunks.assert_awaited_once()
    # PDF projection uploads document.md + manifest.json (no images here).
    assert service.storage.upload_file.await_count == 2


@pytest.mark.asyncio
async def test_process_file_async_marks_failed_in_own_uow_on_error():
    """A processing failure marks FAILED in a dedicated short UoW and re-raises."""
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="scan.pdf",
        path="/manuals/scan.pdf",
        mime_type="application/pdf",
        file_metadata={},
    )

    # get_model, claim, mark_failed.
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    service.storage.iter_download = _make_iter_download_raising(
        RuntimeError("storage down")
    )

    with pytest.raises(RuntimeError, match="storage down"):
        await service.process_file_async(file_id)

    # get_model + claim + mark_failed, all closed.
    assert factory.opened == 3
    assert factory.active == 0


@pytest.mark.asyncio
async def test_process_file_async_terminally_fails_missing_original():
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="missing.pdf",
        path="/manuals/missing.pdf",
        mime_type="application/pdf",
        file_metadata={},
        content_sha256="a" * 64,
    )
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    service.storage.iter_download = _make_iter_download_raising(
        DatastoreObjectNotFoundError("missing")
    )

    with pytest.raises(DatastoreObjectNotFoundError):
        await service.process_file_async(file_id)

    terminal_stmt = factory.sessions[-1].execute.await_args.args[0]
    compiled = str(terminal_stmt.compile().params)
    assert "FAILED_PERMANENT" in compiled
    assert "content_sha256" in str(terminal_stmt)
    assert "processing_attempts" in str(terminal_stmt)
    service.document_processor.extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_file_async_terminally_fails_corrupt_original():
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="corrupt.pdf",
        path="/manuals/corrupt.pdf",
        mime_type="application/pdf",
        file_metadata={},
        content_sha256="0" * 64,
    )
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult(), _ExecuteResult()]
    )
    service = _build_service(factory)
    _set_source_bytes(service, b"tampered bytes")

    with pytest.raises(DatastoreObjectIntegrityError):
        await service.process_file_async(file_id)

    terminal_stmt = factory.sessions[-1].execute.await_args.args[0]
    assert "FAILED_PERMANENT" in str(terminal_stmt.compile().params)
    service.document_processor.extract.assert_not_awaited()


def test_processing_error_summary_never_persists_provider_payload():
    canary = "CANARY_DATASTORE_PROVIDER_SECRET"

    summary = DatastoreFileProcessingService._sanitize_error(
        RuntimeError(f"provider response api_key={canary}")
    )

    assert summary == "RuntimeError: document processing failed"
    assert canary not in summary


@pytest.mark.asyncio
async def test_process_file_async_terminally_fails_oversize_file():
    """A file larger than the size cap is FAILED_PERMANENT without being claimed
    or downloaded, so it never enters the processing/recovery loop or holds
    memory."""
    file_id = uuid4()
    file_model = SimpleNamespace(
        id=file_id,
        kind="FILE",
        status="PENDING",
        search_enabled=True,
        name="huge.pdf",
        path="/manuals/huge.pdf",
        mime_type="application/pdf",
        file_metadata={},
        size_bytes=10**12,  # 1 TB, far over the 100 MB default cap
    )

    # get_model + mark_failed_permanent — no claim, no extraction.
    factory = _RecordingUowFactory(
        results=[_ScalarResult(file_model), _ExecuteResult()]
    )
    service = _build_service(factory)

    await service.process_file_async(file_id)

    service.storage.download_file.assert_not_awaited()
    service.document_processor.extract.assert_not_awaited()
    # Only get_model + the terminal-fail UoW were opened.
    assert factory.opened == 2
    assert factory.active == 0
    # The terminal-fail statement carried FAILED_PERMANENT.
    terminal_stmt = factory.sessions[-1].execute.await_args.args[0]
    compiled = str(terminal_stmt.compile().params)
    assert "FAILED_PERMANENT" in compiled
