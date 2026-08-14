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
    _safe_file_name,
)


def _event(attachments: list[dict]) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="t1",
        message_text="here are some files",
        metadata={"attachments": attachments},
    )


class _FakeAdapter:
    def __init__(self, *, results: dict[str, tuple[bytes, str, str] | None] | None = None,
                 raise_for: set[str] | None = None):
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
    def __init__(self):
        self.created: list[dict] = []

    async def create_file(self, *, pod_id, name, file_content, ctx, directory_path,
                          search_enabled=True, **kwargs):
        self.created.append(
            {
                "pod_id": pod_id,
                "name": name,
                "size": len(file_content),
                "directory_path": directory_path,
            }
        )
        return SimpleNamespace(path=f"{directory_path}/{name}", name=name)


def _service() -> SurfaceFileIngestService:
    return SurfaceFileIngestService(adapter_registry=SimpleNamespace(get=lambda p: None))


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

    saved = await service._ingest_all(
        adapter=adapter,
        pod_id=pod_id,
        platform="TELEGRAM",
        parsed=_event([{"file_id": "a1"}, {"file_id": "a2"}]),
        credentials={},
        file_service=file_service,
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "a1"}, {"file_id": "a2"}],
    )

    assert [item.path for item in saved] == [
        "/me/telegram/report.pdf",
        "/me/telegram/data.csv",
    ]
    assert [c["directory_path"] for c in file_service.created] == [
        "/me/telegram",
        "/me/telegram",
    ]
    assert file_service.created[0]["pod_id"] == pod_id


async def test_ingest_skips_attachment_whose_declared_size_exceeds_cap():
    service = _service()
    adapter = _FakeAdapter(results={"big": (b"x", "big.bin", "application/octet-stream")})
    file_service = _FakeFileService()

    saved = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([]),
        credentials={},
        file_service=file_service,
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "big", "size": INBOUND_ATTACHMENT_BYTE_CAP + 1}],
    )

    assert saved == []
    assert adapter.calls == []  # never even downloaded
    assert file_service.created == []


async def test_ingest_isolates_download_failure_and_continues():
    service = _service()
    adapter = _FakeAdapter(
        results={"ok": (b"data", "ok.txt", "text/plain")},
        raise_for={"bad"},
    )
    file_service = _FakeFileService()

    saved = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([]),
        credentials={},
        file_service=file_service,
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "bad"}, {"file_id": "ok"}],
    )

    # The failing download is skipped; the good one still lands.
    assert [item.path for item in saved] == ["/me/telegram/ok.txt"]


async def test_ingest_skips_when_adapter_returns_none():
    service = _service()
    adapter = _FakeAdapter(results={"x": None})
    file_service = _FakeFileService()

    saved = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([]),
        credentials={},
        file_service=file_service,
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "x"}],
    )

    assert saved == []
    assert file_service.created == []


async def test_ingest_carries_audio_bytes_for_voice_and_not_for_docs():
    service = _service()
    adapter = _FakeAdapter(
        results={
            "voice": (b"OGGAUDIO", "note.ogg", "audio/ogg"),
            "doc": (b"%PDF", "report.pdf", "application/pdf"),
        }
    )
    file_service = _FakeFileService()

    saved = await service._ingest_all(
        adapter=adapter,
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([]),
        credentials={},
        file_service=file_service,
        ctx=SimpleNamespace(),
        attachments=[
            {"file_id": "voice", "content_type": "voice"},
            {"file_id": "doc"},
        ],
    )

    by_path = {item.path: item for item in saved}
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


async def test_the_connection_is_released_before_every_attachment_download():
    """One release per attachment, each before its download.

    The loop runs inside a transaction that has already written rows, and each
    attachment is up to 50 MB fetched over a 60s-timeout HTTP call. Holding the
    connection across that pins it — and the row locks — for the whole
    download, once per attachment.

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

    async def _release() -> None:
        order.append("release")

    await service._ingest_all(
        adapter=_RecordingAdapter(),
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event([{"file_id": "a1"}, {"file_id": "a2"}]),
        credentials={},
        file_service=_FakeFileService(),
        ctx=SimpleNamespace(),
        attachments=[{"file_id": "a1"}, {"file_id": "a2"}],
        release=_release,
    )

    assert order == ["release", "download", "release", "download"]
