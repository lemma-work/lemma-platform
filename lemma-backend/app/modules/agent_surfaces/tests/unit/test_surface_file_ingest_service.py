from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.attachment_limits import (
    INBOUND_ATTACHMENT_BYTE_CAP,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    SurfaceFileIngestService,
    _numbered,
    _safe_file_name,
)
from app.modules.datastore.contracts import DatastoreConflictError


def _event(attachments: list[dict]) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="t1",
        message_text="here are some files",
        metadata={"attachments": attachments},
    )


class _FakeAdapter:
    def __init__(
        self,
        *,
        results: dict[str, tuple[bytes, str, str] | None] | None = None,
        raise_for: set[str] | None = None,
    ):
        self.results = results or {}
        self.raise_for = raise_for or set()
        self.calls: list[dict] = []

    async def download_attachment(self, *, credentials, event, attachment):
        key = str(attachment.get("file_id") or attachment.get("id") or "")
        self.calls.append(attachment)
        if key in self.raise_for:
            raise RuntimeError("boom")
        return self.results.get(key)


class _FakeFileService:
    """Stands in for the datastore writer, including its refusal of a duplicate path."""

    def __init__(self):
        self.created: list[dict] = []
        self.taken: set[str] = set()

    async def write(
        self,
        *,
        pod_id,
        name,
        content,
        ctx,
        directory_path,
        search_enabled=True,
        **kwargs,
    ):
        path = f"{directory_path}/{name}"
        if path in self.taken:
            raise DatastoreConflictError(f"A file or folder already exists at '{path}'")
        self.taken.add(path)
        self.created.append(
            {
                "pod_id": pod_id,
                "name": name,
                "size": len(content),
                "directory_path": directory_path,
            }
        )
        return SimpleNamespace(path=path, name=name)


def _direct_store(file_service):
    """The unit-test `store`: no transaction, just hand the fake writer over."""

    async def _store(persist_ingested_attachment):
        return await persist_ingested_attachment(file_service.write)

    return _store


def _service() -> SurfaceFileIngestService:
    return SurfaceFileIngestService(
        adapter_registry=SimpleNamespace(get=lambda p: None)
    )


async def test_ingest_all_writes_files_to_me_platform_folder():
    service = _service()
    adapter = _FakeAdapter(
        results={
            "a1": (b"hello", "report.pdf", "application/pdf"),
            "a2": (b"world!!", "data.csv", "text/csv"),
        }
    )
    file_service = _FakeFileService()
    pod_id = uuid4()

    outcome = await service._ingest_all(
        adapter=adapter,
        pod_id=pod_id,
        platform="TELEGRAM",
        parsed=_event([{"file_id": "a1"}, {"file_id": "a2"}]),
        credentials={},
        store=_direct_store(file_service),
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "a1"}, {"file_id": "a2"}],
    )

    assert [item.path for item in outcome.saved] == [
        "/me/telegram/report.pdf",
        "/me/telegram/data.csv",
    ]
    assert [c["directory_path"] for c in file_service.created] == [
        "/me/telegram",
        "/me/telegram",
    ]
    assert file_service.created[0]["pod_id"] == pod_id


async def test_an_oversize_attachment_is_reported_rather_than_dropped():
    service = _service()
    adapter = _FakeAdapter(
        results={"big": (b"x", "big.bin", "application/octet-stream")}
    )
    file_service = _FakeFileService()

    outcome = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([]),
        credentials={},
        store=_direct_store(file_service),
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "big", "size": INBOUND_ATTACHMENT_BYTE_CAP + 1}],
    )

    assert outcome.saved == []
    assert adapter.calls == []  # never even downloaded
    assert file_service.created == []
    # Reported, so the agent can say why rather than ignoring the file.
    assert [(f.name, f.reason) for f in outcome.failed] == [
        ("the file", "it is larger than the 50 MB limit")
    ]


async def test_ingest_isolates_download_failure_and_continues():
    service = _service()
    adapter = _FakeAdapter(
        results={"ok": (b"data", "ok.txt", "text/plain")},
        raise_for={"bad"},
    )
    file_service = _FakeFileService()

    outcome = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([]),
        credentials={},
        store=_direct_store(file_service),
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "bad"}, {"file_id": "ok"}],
    )

    # The failing download does not sink the good one, and is still named.
    assert [item.path for item in outcome.saved] == ["/me/telegram/ok.txt"]
    assert [(f.name, f.reason) for f in outcome.failed] == [
        ("the file", "the download failed")
    ]


async def test_an_undownloadable_attachment_is_reported_rather_than_dropped():
    service = _service()
    adapter = _FakeAdapter(results={"x": None})
    file_service = _FakeFileService()

    outcome = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([]),
        credentials={},
        store=_direct_store(file_service),
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "x"}],
    )

    assert outcome.saved == []
    assert file_service.created == []
    assert [f.reason for f in outcome.failed] == ["the download failed"]


async def test_ingest_carries_audio_bytes_for_voice_and_not_for_docs():
    service = _service()
    adapter = _FakeAdapter(
        results={
            "voice": (b"OGGAUDIO", "note.ogg", "audio/ogg"),
            "doc": (b"%PDF", "report.pdf", "application/pdf"),
        }
    )
    file_service = _FakeFileService()

    outcome = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([]),
        credentials={},
        store=_direct_store(file_service),
        ctx=SimpleNamespace(),
        attachments=[
            {"file_id": "voice", "content_type": "voice"},
            {"file_id": "doc"},
        ],
    )

    by_path = {item.path: item for item in outcome.saved}
    voice = by_path["/me/telegram/note.ogg"]
    assert voice.is_audio is True
    assert voice.audio_bytes == b"OGGAUDIO"  # carried for transcription
    doc = by_path["/me/telegram/report.pdf"]
    assert doc.is_audio is False
    assert doc.audio_bytes is None


def test_safe_file_name_strips_paths_and_falls_back():
    assert _safe_file_name("report.pdf") == "report.pdf"
    assert _safe_file_name("/etc/passwd") == "passwd"
    assert _safe_file_name("a/b/c.txt") == "c.txt"
    assert _safe_file_name("") == "attachment"
    assert _safe_file_name(None) == "attachment"


def test_safe_file_name_completes_a_nameless_photo_from_its_mime_type():
    """A chat photo arrives with no filename; the datastore types by name only.

    Saved as bare ``image``/``photo`` the file comes back as
    ``application/octet-stream``: ``view_image`` refuses it and indexing skips
    it, so the agent holds a path to something it cannot open.
    """
    assert _safe_file_name("image", "image/jpeg") == "image.jpg"
    assert _safe_file_name("photo", "image/png") == "photo.png"
    assert _safe_file_name("voice", "audio/ogg; codecs=opus") == "voice.ogg"
    # An extension the sender supplied is left exactly as it is.
    assert _safe_file_name("report.pdf", "application/pdf") == "report.pdf"
    # "octet-stream" is the absence of a type; inventing ".bin" would look decided.
    assert _safe_file_name("image", "application/octet-stream") == "image"
    assert _safe_file_name("image", None) == "image"


def test_numbered_puts_the_counter_before_the_extension():
    assert _numbered("image.jpg", 0) == "image.jpg"
    assert _numbered("image.jpg", 1) == "image-2.jpg"
    assert _numbered("report.tar.gz", 2) == "report.tar-3.gz"
    assert _numbered("attachment", 1) == "attachment-2"


async def test_a_second_photo_with_the_same_name_is_still_saved():
    """Every WhatsApp photo is called "image"; every Telegram one, "photo".

    The datastore refuses a duplicate path, so the second one anyone ever sent
    used to be dropped — logged as a warning, and the agent told only about the
    first.
    """
    service = _service()
    adapter = _FakeAdapter(
        results={
            "p1": (b"one", "photo", "image/png"),
            "p2": (b"two", "photo", "image/png"),
        }
    )
    file_service = _FakeFileService()
    attachments = [{"file_id": "p1"}, {"file_id": "p2"}]

    outcome = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event(attachments),
        credentials={},
        store=_direct_store(file_service),
        ctx=SimpleNamespace(),
        attachments=attachments,
    )

    assert [item.path for item in outcome.saved] == [
        "/me/telegram/photo.png",
        "/me/telegram/photo-2.png",
    ]


async def test_a_nameless_photo_is_stored_under_a_typed_name():
    service = _service()
    adapter = _FakeAdapter(results={"p1": (b"\x89PNG", "photo", "image/png")})
    file_service = _FakeFileService()

    outcome = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([{"file_id": "p1"}]),
        credentials={},
        store=_direct_store(file_service),
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "p1"}],
    )

    assert [item.path for item in outcome.saved] == ["/me/telegram/photo.png"]


async def test_no_transaction_is_open_while_an_attachment_downloads():
    """Download first, open the write transaction second — once per attachment.

    Each attachment is up to 50 MB fetched over a 60s-timeout HTTP call. The
    previous shape ran the loop inside one transaction and committed before
    every download to hand the connection back; that was correct but only
    legible as a comment. Now the download simply has no transaction around it.

    An ordering assertion, not a timing one: the fake adapter returns instantly
    and always will, so only the sequence can carry the property.
    """
    service = _service()
    adapter = _FakeAdapter(
        results={
            "a1": (b"hello", "report.pdf", "application/pdf"),
            "a2": (b"world!!", "data.csv", "text/csv"),
        }
    )
    order: list[str] = []

    class _RecordingAdapter:
        async def download_attachment(self, **kwargs):
            order.append("download")
            return await adapter.download_attachment(**kwargs)

    file_service = _FakeFileService()

    async def _store(persist):
        order.append("open")
        try:
            return await persist(file_service.write)
        finally:
            order.append("close")

    outcome = await service._ingest_all(
        adapter=_RecordingAdapter(),
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([{"file_id": "a1"}, {"file_id": "a2"}]),
        credentials={},
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "a1"}, {"file_id": "a2"}],
        store=_store,
    )

    assert len(outcome.saved) == 2
    assert order == [
        "download",
        "open",
        "close",
        "download",
        "open",
        "close",
    ]


async def test_a_failed_download_opens_no_transaction_at_all():
    """The write scope is never entered for a file that never arrived."""
    service = _service()

    class _FailingAdapter:
        async def download_attachment(self, **kwargs):
            raise RuntimeError("boom")

    opened = 0

    async def _store(persist):
        nonlocal opened
        opened += 1
        return await persist(_FakeFileService().write)

    outcome = await service._ingest_all(
        adapter=_FailingAdapter(),
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([{"file_id": "a1"}]),
        credentials={},
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "a1"}],
        store=_store,
    )

    assert outcome.saved == []
    assert opened == 0
    assert [f.reason for f in outcome.failed] == ["the download failed"]
