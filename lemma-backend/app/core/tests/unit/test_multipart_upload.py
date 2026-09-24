"""Large uploads must survive a real multipart API. Proved against moto's S3 server.

GCS and S3 reject any multipart part but the last that is under 5 MiB. The local
filesystem store used by the rest of the suite has no such rule, so it accepted
a 1 MiB chunk size happily — and that chunk size shipped, and broke every
datastore file upload over 1 MiB in production with::

    EntityTooSmall: Part 1 has size of 1048576 bytes, which is smaller than
                    min part size (5242880 bytes)

Under 1 MiB an upload is a single part, and the last part is exempt from the
minimum, so small files passed and hid it for as long as nobody uploaded
anything bigger.

moto enforces the same minimum, so these tests fail on the old chunk size and
pass on the fix. It runs in-process on a thread: this used to be a MinIO
container in the e2e lane, which cost a 228 MB image pull for four tests, and
broke every e2e shard at once when upstream stopped serving that image
anonymously. Each payload is deliberately larger than 5 MiB: below that there is
no second part and the rule never engages, which is exactly how the bug stayed
invisible.

Every adapter that uploads a staged file is covered, not just the one that
failed in production — the same override was on all three.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

import obstore as obs
import pytest
from moto.server import ThreadedMotoServer

from app.core.object_storage import build_object_store, reset_object_stores
from app.modules.apps.services.app_file_manager import AppFileManager
from app.modules.datastore.infrastructure.storage import ObstoreDatastoreStorage
from app.modules.pod_bundle.infrastructure.staging import BundleStagingStorage

pytestmark = pytest.mark.asyncio

_BUCKET = "lemma-multipart"

#: Bigger than the 5 MiB minimum, so a second part is genuinely required. At the
#: old 1 MiB chunk size this is seven parts, six of them illegal.
_PAYLOAD_BYTES = 7 * 1024 * 1024


@pytest.fixture
def staged_payload() -> Path:
    """A file on disk: only a Path triggers the multipart path (`use_multipart`)."""
    handle, name = tempfile.mkstemp(suffix=".bin")
    os.close(handle)
    path = Path(name)
    # Not all one byte value — a store that silently truncated would still match
    # on length, so the digest has to be able to tell.
    path.write_bytes(bytes(range(256)) * (_PAYLOAD_BYTES // 256))
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def s3_endpoint():
    """moto's S3 server on a free port, with the test bucket already created.

    obstore does not create buckets, and moto accepts an unsigned ``PUT /<bucket>``,
    so no S3 client is needed for provisioning.
    """
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    try:
        host, port = server.get_host_and_port()
        endpoint = f"http://{host}:{port}"
        urllib.request.urlopen(
            urllib.request.Request(f"{endpoint}/{_BUCKET}", method="PUT")
        ).close()
        yield endpoint
    finally:
        server.stop()


@pytest.fixture
def s3_store(s3_endpoint, monkeypatch):
    """A store pointed at moto, built the way production builds one."""
    from app.core.config import settings

    # obstore's S3 backend reads credentials from the environment, the same way
    # it does in a deployment that uses an instance role or injected secrets.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(settings, "storage_endpoint_url", s3_endpoint)
    monkeypatch.setattr(settings, "storage_bucket", _BUCKET)

    reset_object_stores()
    try:
        yield build_object_store(
            local_prefix="unused-for-s3",
            bucket_name=_BUCKET,
            force_backend="s3",
        )
    finally:
        reset_object_stores()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _read_back(store, key: str) -> bytes:
    response = await obs.get_async(store, key)
    return (await response.bytes_async()).to_bytes()


async def test_a_datastore_file_larger_than_one_part_uploads(s3_store, staged_payload):
    """The path production was failing: POST /pods/{id}/datastore/files."""
    storage = ObstoreDatastoreStorage(s3_store)

    await storage.upload_file("datastore/large-upload.bin", staged_payload)

    stored = await _read_back(s3_store, "datastore/large-upload.bin")
    assert len(stored) == _PAYLOAD_BYTES
    assert hashlib.sha256(stored).hexdigest() == _digest(staged_payload)


async def test_an_app_bundle_larger_than_one_part_uploads(s3_store, staged_payload):
    """The path development found, on POST /pods/{id}/apps/{name}/bundle."""
    from uuid import uuid4

    manager = AppFileManager(uuid4(), store=s3_store)

    result = await manager.write_file("dist/bundle.tar.gz", staged_payload)

    assert result["size"] == _PAYLOAD_BYTES
    stored = await _read_back(s3_store, manager._key("dist/bundle.tar.gz"))
    assert len(stored) == _PAYLOAD_BYTES


async def test_a_pod_bundle_staging_archive_larger_than_one_part_uploads(
    s3_store, staged_payload
):
    """The third call site, which nothing had yet exercised at this size."""
    from uuid import uuid4

    storage = BundleStagingStorage(store=s3_store)

    key = await storage.put_archive("export", uuid4(), staged_payload)

    stored = await _read_back(s3_store, key)
    assert len(stored) == _PAYLOAD_BYTES


async def test_the_old_chunk_size_still_fails_here(s3_store, staged_payload):
    """Proof the harness can see the bug, not just that the fix passes.

    Without this, an S3 fake that had quietly stopped enforcing the minimum would
    make every test above pass for the wrong reason.
    """
    with pytest.raises(Exception) as failure:
        await obs.put_async(
            s3_store,
            "datastore/regression-probe.bin",
            staged_payload,
            use_multipart=True,
            chunk_size=1024 * 1024,
        )

    # Asserted on `uploadId=` rather than the server's wording: that query
    # parameter is S3 API surface, so this pins the failure to the multipart
    # *completion* request — the upload got all the way to assembling the parts
    # and the server rejected them for being too small. Matching on the fake's
    # message text would break on an upgrade without the meaning changing.
    assert "uploadid=" in str(failure.value).lower(), (
        f"expected the multipart completion to be rejected, got: {failure.value}"
    )
