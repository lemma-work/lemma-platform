"""A retried inbound job reuses the files and transcripts its first attempt made.

Ingest and transcription run before the turn's only database write. When that
write failed and the job retried, every file was stored again under the next
free name and every voice note transcribed (and paid for) again.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import fakeredis
import pytest

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent.contracts.speech import VoiceTranscript
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.services.attachment_ingest_memo import (
    AttachmentIngestMemo,
    memo_key,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    IngestedAttachment,
    SurfaceFileIngestService,
)
from app.modules.agent_surfaces.services.surface_inbound_message import (
    transcribe_voice_attachments,
)
from app.modules.datastore.contracts import DatastoreConflictError

pytestmark = pytest.mark.asyncio


def _memo() -> AttachmentIngestMemo:
    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="surface:ingested", ttl_seconds=60
    )
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return AttachmentIngestMemo(cache)


def _event(attachment: dict) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="chat-1",
        external_thread_id="chat-1",
        external_message_id="msg-77",
        message_text="",
        metadata={"attachments": [attachment]},
    )


class _Adapter:
    def __init__(self, result: tuple[bytes, str, str]):
        self.result = result
        self.downloads = 0

    async def download_attachment(self, *, credentials, event, attachment):
        self.downloads += 1
        return self.result


class _Pod:
    """The datastore writer, refusing a duplicate path the way the real one does."""

    def __init__(self):
        self.stored: list[str] = []

    async def write(self, *, pod_id, name, content, ctx, directory_path, **_):
        path = f"{directory_path}/{name}"
        if path in self.stored:
            raise DatastoreConflictError(path)
        self.stored.append(path)
        return SimpleNamespace(path=path, name=name)

    def store(self):
        async def _store(persist):
            return await persist(self.write)

        return _store


async def _attempt(service, adapter, pod, pod_id, attachment):
    return await service._ingest_all(
        adapter=adapter,
        pod_id=pod_id,
        platform="TELEGRAM",
        parsed=_event(attachment),
        credentials={},
        ctx=SimpleNamespace(),
        attachments=[attachment],
        store=pod.store(),
    )


async def test_a_retried_message_reuses_the_file_it_already_stored():
    service = SurfaceFileIngestService(
        adapter_registry=SimpleNamespace(get=lambda p: None), memo=_memo()
    )
    adapter = _Adapter((b"%PDF-1.4 receipt", "receipt.pdf", "application/pdf"))
    pod, pod_id = _Pod(), uuid4()
    attachment = {"file_id": "f1", "name": "receipt.pdf"}

    first = await _attempt(service, adapter, pod, pod_id, attachment)
    retried = await _attempt(service, adapter, pod, pod_id, attachment)

    assert pod.stored == ["/me/telegram/receipt.pdf"], "no receipt-2.pdf"
    assert [item.path for item in retried.saved] == [first.saved[0].path]
    assert adapter.downloads == 1


async def test_a_voice_note_is_fetched_again_only_until_its_transcript_is_kept():
    memo = _memo()
    service = SurfaceFileIngestService(
        adapter_registry=SimpleNamespace(get=lambda p: None), memo=memo
    )
    adapter = _Adapter((b"OggS voice", "voice.ogg", "audio/ogg"))
    pod, pod_id = _Pod(), uuid4()
    attachment = {"file_id": "v1", "content_type": "voice"}

    first = await _attempt(service, adapter, pod, pod_id, attachment)
    # The write failed before transcription landed: the bytes are needed again,
    # the file is not stored again.
    second = await _attempt(service, adapter, pod, pod_id, attachment)
    await memo.remember_transcript(
        second.saved[0].memo_key, VoiceTranscript(text="call me back")
    )
    third = await _attempt(service, adapter, pod, pod_id, attachment)

    assert pod.stored == ["/me/telegram/voice.ogg"]
    assert first.saved[0].audio_bytes == second.saved[0].audio_bytes == b"OggS voice"
    assert third.saved[0].audio_bytes is None
    assert adapter.downloads == 2


async def test_a_kept_transcript_is_read_back_instead_of_bought_again():
    memo = _memo()
    key = memo_key(
        pod_id=uuid4(),
        platform="TELEGRAM",
        parsed=_event({"file_id": "v1"}),
        index=0,
    )
    await memo.remember_transcript(key, VoiceTranscript(text="call me back"))
    metadata: dict[str, object] = {}

    text = await transcribe_voice_attachments(
        ingested=[
            IngestedAttachment(
                path="/me/telegram/voice.ogg",
                name="voice.ogg",
                mime="audio/ogg",
                content_type="voice",
                memo_key=key,
            )
        ],
        original_text="",
        metadata=metadata,
        memo=memo,
    )

    assert text == "call me back"
    assert metadata["voice_transcripts"] == [
        {
            "path": "/me/telegram/voice.ogg",
            "text": "call me back",
            "detected_language": None,
            "duration_seconds": None,
        }
    ]
