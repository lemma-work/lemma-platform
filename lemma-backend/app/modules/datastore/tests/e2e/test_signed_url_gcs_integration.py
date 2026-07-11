"""Real-GCS integration test for public signed URLs (egress behavior).

Unlike ``test_signed_url_e2e.py`` (which runs on the local filesystem backend),
this drives the *actual* GCS object store so we exercise the real egress path:
the file is uploaded to GCS, and ``GET /s/{code}`` streams those bytes back from
GCS *through the backend* — which is precisely what makes the hit cap bound
egress (a raw object-store signed URL would bypass it).

Gated: set ``LEMMA_GCS_ITEST_BUCKET`` to a writable GCS bucket and have GCS
credentials available (ADC or a service account). Without it the test is skipped
so normal CI never reaches the cloud. It still needs the e2e infra containers
(Postgres/Redis/SuperTokens) like every other e2e test.

NOTE on V4 signed URLs: the authenticated ``GET .../files/url`` download URL on
GCS is a real object-signed URL, which requires a *service-account* signer. With
plain user ADC (``authorized_user``) signing fails at the IAM ``signBlob`` step,
so this test does not assert that path — the deployed backend (workload identity
/ service account) covers it. The public ``/s/{code}`` path needs only read
access and is what we verify here.
"""

from __future__ import annotations

import os

import pytest
from fastapi import status
from httpx import AsyncClient

from app.core.config import settings
from app.modules.datastore.infrastructure.storage import create_datastore_storage
from app.modules.datastore.tests.e2e.harness import DatastoreApi

_BUCKET = os.getenv("LEMMA_GCS_ITEST_BUCKET")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not _BUCKET,
        reason="set LEMMA_GCS_ITEST_BUCKET (+ GCS creds) to run the real-GCS integration test",
    ),
]


@pytest.fixture
def gcs_backend(monkeypatch):
    """Force the datastore storage backend to the real GCS bucket for this test.

    ``create_datastore_storage()`` reads these settings per request, so flipping
    them here routes upload + serving through GCS without rebuilding the app.
    """
    monkeypatch.setattr(settings, "storage_backend", "gcs")
    monkeypatch.setattr(settings, "storage_bucket", _BUCKET)
    yield


class TestSignedUrlAgainstRealGcs:
    @pytest.mark.asyncio
    async def test_signed_url_streams_from_real_gcs_and_enforces_cap(
        self, gcs_backend, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        content = b"real gcs egress payload \x00\x01\x02" * 64  # ~1.7 KiB, binary
        folder = await pod_api.create_folder("/me/gcs-itest")
        uploaded = await pod_api.upload_file(
            "blob.dat",
            content,
            directory_path=folder["path"],
            content_type="application/octet-stream",
            search_enabled=False,
        )

        try:
            resp = await pod_api.request(
                "POST",
                f"/pods/{pod_api.pod_id}/datastore/files/signed-url",
                params={"path": uploaded["path"]},
                json={"max_hits": 2},
            )
            assert resp.status_code == status.HTTP_201_CREATED, resp.text
            code = resp.json()["signed_url"].rstrip("/").rsplit("/", 1)[-1]

            # Exactly max_hits fetches stream the real GCS bytes through the backend.
            for _ in range(2):
                served = await async_client.get(f"/s/{code}")
                assert served.status_code == status.HTTP_200_OK, served.text
                assert served.content == content

            # One past the cap → 410 (egress is bounded even on GCS).
            gone = await async_client.get(f"/s/{code}")
            assert gone.status_code == status.HTTP_410_GONE
        finally:
            # Remove the objects this test wrote to the shared bucket.
            storage = create_datastore_storage()
            await storage.delete_prefix(f"pods/{pod_api.pod_id}/")
