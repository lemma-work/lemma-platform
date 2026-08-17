"""Core E2E fixtures.

The MinIO fixture exists for one reason: multipart part-size rules are enforced
by the object store, not by the client, so the local filesystem store used
elsewhere in the suite cannot catch an upload chunk size the real API rejects.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import pytest

from app.core.test_utils import (
    MINIO_ROOT_PASSWORD,
    MINIO_ROOT_USER,
    get_minio_container,
)

_BUCKET = "lemma-e2e"


@dataclass(frozen=True)
class MinioSettings:
    endpoint_url: str
    bucket: str
    access_key: str
    secret_key: str


def _provision_bucket(container_id: str) -> None:
    """Create the test bucket with the ``mc`` client already in the image.

    ``127.0.0.1`` rather than ``localhost``: inside the container the name
    resolves to ``::1`` first and MinIO listens on IPv4, so the alias fails with
    a connection refused that looks like a startup race but never resolves.

    Retried because an open TCP port is not the same as a ready MinIO — the
    server accepts the connection several hundred milliseconds before it will
    answer an API call.
    """
    deadline = time.monotonic() + 60
    last_error = b""
    while time.monotonic() < deadline:
        alias = subprocess.run(
            ["docker", "exec", container_id, "mc", "alias", "set", "local",
             "http://127.0.0.1:9000", MINIO_ROOT_USER, MINIO_ROOT_PASSWORD],
            capture_output=True,
        )
        if alias.returncode == 0:
            made = subprocess.run(
                ["docker", "exec", container_id, "mc", "mb", "--ignore-existing",
                 f"local/{_BUCKET}"],
                capture_output=True,
            )
            if made.returncode == 0:
                return
            last_error = made.stderr
        else:
            last_error = alias.stderr
        time.sleep(0.5)
    raise RuntimeError(
        f"MinIO never became ready to provision {_BUCKET!r}: "
        f"{last_error.decode('utf-8', errors='replace')}"
    )


@pytest.fixture(scope="session")
def _minio_container():
    with get_minio_container() as container:
        # obstore does not create buckets, so provisioning happens in-container
        # rather than pulling an S3 client into the test dependencies.
        _provision_bucket(container.container_id or "")
        yield container


@pytest.fixture
def minio_settings(_minio_container, monkeypatch) -> MinioSettings:
    """Configure the app to talk to MinIO exactly as it would talk to S3."""
    from app.core.config import settings

    host = _minio_container.get_container_host_ip()
    port = _minio_container.get_exposed_port(9000)
    endpoint = f"http://{host}:{port}"

    # obstore's S3 backend reads credentials from the environment, the same way
    # it does in a deployment that uses an instance role or injected secrets.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", MINIO_ROOT_USER)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", MINIO_ROOT_PASSWORD)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(settings, "storage_endpoint_url", endpoint)
    monkeypatch.setattr(settings, "storage_bucket", _BUCKET)

    return MinioSettings(
        endpoint_url=endpoint,
        bucket=_BUCKET,
        access_key=MINIO_ROOT_USER,
        secret_key=MINIO_ROOT_PASSWORD,
    )
