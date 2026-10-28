"""Where an Agent Host's bulk bytes wait, and how an image event finds them.

The store's rules -- whose transfer, which direction, used once, the bytes it
was described by -- live in Redis scripts, so these run them for real in
fakeredis rather than restating them. The HTTP routes in front of the store
are ``test_agent_host_transfers_e2e.py``.
"""

from __future__ import annotations

import base64
import hashlib
from uuid import uuid4, uuid7

import pytest
from fakeredis.aioredis import FakeRedis

from app.modules.agent.agent_host.domain.transfer import BLOB_FIELD
from app.modules.agent.agent_host.control.transfers import (
    AgentHostTransferStore,
    TransferRefusal,
    TransferRefused,
)
from app.modules.agent.agent_host.harness.artifacts import (
    PodFileAgentHostArtifactWriter,
    _inline_images,
)

pytestmark = pytest.mark.asyncio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
async def store():
    redis = FakeRedis(decode_responses=False)
    yield AgentHostTransferStore(client=redis)
    await redis.aclose()


async def _refused(call) -> TransferRefusal:
    with pytest.raises(TransferRefused) as raised:
        await call
    return raised.value.refusal


class TestTransfersToTheHost:
    async def test_the_host_it_was_made_for_fetches_it_once(self, store) -> None:
        host = uuid4()
        staged = await store.stage_for_host(
            host_id=host, workspace=uuid4(), data=b"chunk", ttl_seconds=60
        )
        assert staged.wire() == {"id": staged.id, "size": 5, "sha256": _sha(b"chunk")}
        assert await store.fetch_by_host(staged.id, host_id=host) == (
            b"chunk",
            _sha(b"chunk"),
        )
        assert (
            await _refused(store.fetch_by_host(staged.id, host_id=host))
            is TransferRefusal.ALREADY_USED
        )

    async def test_another_host_learns_nothing_about_it(self, store) -> None:
        staged = await store.stage_for_host(
            host_id=uuid4(), workspace=uuid4(), data=b"x", ttl_seconds=60
        )
        assert (
            await _refused(store.fetch_by_host(staged.id, host_id=uuid4()))
            is TransferRefusal.NOT_FOUND
        )
        assert (
            await _refused(store.fetch_by_host("never-made", host_id=uuid4()))
            is TransferRefusal.NOT_FOUND
        )

    async def test_a_fetch_cannot_be_filled_and_a_slot_cannot_be_fetched(
        self, store
    ) -> None:
        host = uuid4()
        staged = await store.stage_for_host(
            host_id=host, workspace=uuid4(), data=b"x", ttl_seconds=60
        )
        slot = await store.open_for_host(
            host_id=host, workspace=uuid4(), max_bytes=4, ttl_seconds=60
        )
        assert (
            await _refused(
                store.deliver_by_host(
                    staged.id, host_id=host, data=b"y", sha256=_sha(b"y")
                )
            )
            is TransferRefusal.NOT_FOUND
        )
        assert (
            await _refused(store.fetch_by_host(slot.id, host_id=host))
            is TransferRefusal.NOT_FOUND
        )


class TestTransfersFromTheHost:
    async def test_a_slot_takes_what_it_was_opened_for_once(self, store) -> None:
        host = uuid4()
        slot = await store.open_for_host(
            host_id=host, workspace=uuid4(), max_bytes=4, ttl_seconds=60
        )
        assert await store.max_bytes_for(slot.id, host_id=host) == 4
        assert (
            await _refused(
                store.deliver_by_host(
                    slot.id, host_id=host, data=b"12345", sha256=_sha(b"12345")
                )
            )
            is TransferRefusal.TOO_LARGE
        )
        assert (
            await _refused(
                store.deliver_by_host(
                    slot.id, host_id=host, data=b"12", sha256=_sha(b"x")
                )
            )
            is TransferRefusal.DIGEST_MISMATCH
        )
        await store.deliver_by_host(
            slot.id, host_id=host, data=b"12", sha256=_sha(b"12")
        )
        assert (
            await _refused(
                store.deliver_by_host(
                    slot.id, host_id=host, data=b"34", sha256=_sha(b"34")
                )
            )
            is TransferRefusal.ALREADY_USED
        )
        assert await store.collect(slot.id, size=2, sha256=_sha(b"12")) == b"12"
        # Collected, it is gone.
        assert (
            await _refused(store.max_bytes_for(slot.id, host_id=host))
            is TransferRefusal.NOT_FOUND
        )

    async def test_an_answer_for_bytes_that_never_arrived_is_refused(
        self, store
    ) -> None:
        host = uuid4()
        empty = await store.open_for_host(
            host_id=host, workspace=uuid4(), max_bytes=4, ttl_seconds=60
        )
        assert (
            await _refused(store.collect(empty.id, size=2, sha256=_sha(b"12")))
            is TransferRefusal.NOT_FOUND
        )
        other = await store.open_for_host(
            host_id=host, workspace=uuid4(), max_bytes=4, ttl_seconds=60
        )
        await store.deliver_by_host(
            other.id, host_id=host, data=b"12", sha256=_sha(b"12")
        )
        assert (
            await _refused(store.collect(other.id, size=2, sha256=_sha(b"34")))
            is TransferRefusal.DIGEST_MISMATCH
        )

    async def test_another_host_cannot_fill_it(self, store) -> None:
        slot = await store.open_for_host(
            host_id=uuid4(), workspace=uuid4(), max_bytes=4, ttl_seconds=60
        )
        assert (
            await _refused(store.max_bytes_for(slot.id, host_id=uuid4()))
            is TransferRefusal.NOT_FOUND
        )
        assert (
            await _refused(
                store.deliver_by_host(
                    slot.id, host_id=uuid4(), data=b"1", sha256=_sha(b"1")
                )
            )
            is TransferRefusal.NOT_FOUND
        )


class _Blobs:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    async def get_blob(self, *, run_id, sha256):
        return self.blobs.get(sha256)


class TestImageEvents:
    async def test_a_blob_is_kept_under_its_run_and_digest(self, store) -> None:
        run_id = uuid7()
        await store.put_blob(run_id=run_id, sha256=_sha(PNG), data=PNG)
        assert await store.get_blob(run_id=run_id, sha256=_sha(PNG)) == PNG
        assert await store.get_blob(run_id=uuid7(), sha256=_sha(PNG)) is None
        assert (
            await _refused(store.put_blob(run_id=run_id, sha256=_sha(b"x"), data=PNG))
            is TransferRefusal.DIGEST_MISMATCH
        )

    async def test_an_image_named_by_its_blob_is_read_from_the_blob(self) -> None:
        payload = {
            "content": [
                {
                    "type": "image",
                    "mimeType": "image/png",
                    BLOB_FIELD: {"sha256": _sha(PNG), "size": len(PNG)},
                },
                {
                    "type": "resource",
                    "resource": {
                        "uri": "file:///x.png",
                        "mimeType": "image/png",
                        BLOB_FIELD: {"sha256": _sha(PNG), "size": len(PNG)},
                    },
                },
                # An older host's, inline: still read.
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": base64.b64encode(PNG).decode(),
                },
            ]
        }
        images = _inline_images(payload)
        assert [image.blob_sha256 for image in images] == [_sha(PNG), _sha(PNG), None]
        writer = PodFileAgentHostArtifactWriter(
            uow_factory=None, blobs=_Blobs({_sha(PNG): PNG})
        )
        for image in images:
            assert await writer._image_bytes(image, agent_run_id=uuid7()) == PNG

    async def test_a_blob_that_is_missing_or_not_the_image_is_a_warning(
        self,
    ) -> None:
        image = _inline_images(
            {
                "content": {
                    "type": "image",
                    "mimeType": "image/png",
                    BLOB_FIELD: {"sha256": _sha(PNG), "size": len(PNG)},
                }
            }
        )[0]
        missing = PodFileAgentHostArtifactWriter(uow_factory=None, blobs=_Blobs({}))
        with pytest.raises(ValueError, match="expired"):
            await missing._image_bytes(image, agent_run_id=uuid7())
        not_png = PodFileAgentHostArtifactWriter(
            uow_factory=None, blobs=_Blobs({_sha(PNG): b"GIF89a"})
        )
        with pytest.raises(ValueError, match="do not match"):
            await not_png._image_bytes(image, agent_run_id=uuid7())
