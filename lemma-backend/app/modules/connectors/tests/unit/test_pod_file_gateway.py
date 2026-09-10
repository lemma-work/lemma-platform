"""The mapping between datastore's file operations and connectors' file port.

The whole of `DatastorePodFileGateway` is this translation, so the two datastore
operations are injected rather than patched: a double installed on this module
would stand in for the only thing under test.

Lived in `app/composition/tests/unit/test_lazy_composition_adapters.py`, where
the seam it needed was a `monkeypatch` of `datastore.api.dependencies`. It got
that for free from a lazy import inside the adapter's constructor -- an import
placed there to dodge a module cycle that only existed because the adapter was
in neither module.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.infrastructure.adapters.pod_file_gateway import (
    DatastorePodFileGateway,
)
from app.modules.datastore.contracts.pod_files import PodFileContent, StoredPodFile

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_read_bytes_carries_the_media_type_and_name_alongside_the_content():
    pod_id = uuid4()

    async def read_file(uow, *, pod_id, path, ctx):
        assert (uow, path, ctx) == ("uow", "/report.txt", "ctx")
        return PodFileContent(
            content=b"report", media_type="text/plain", name="report.txt"
        )

    gateway = DatastorePodFileGateway("uow", read_file=read_file)

    assert await gateway.read_bytes(pod_id=pod_id, path="/report.txt", ctx="ctx") == (
        b"report",
        "text/plain",
        "report.txt",
    )


@pytest.mark.asyncio
async def test_write_bytes_returns_the_file_reference_a_result_carries():
    pod_id = uuid4()

    async def write_file(uow, *, pod_id, directory, name, content, ctx):
        assert (uow, directory, name, content, ctx) == (
            "uow",
            "/exports",
            "report.txt",
            b"report",
            "ctx",
        )
        return StoredPodFile(
            pod_path="/exports/report.txt", size_bytes=6, media_type="text/plain"
        )

    gateway = DatastorePodFileGateway("uow", write_file=write_file)

    assert await gateway.write_bytes(
        pod_id=pod_id,
        directory="/exports",
        name="report.txt",
        content=b"report",
        media_type=None,
        ctx="ctx",
    ) == {
        "type": "pod_file",
        "pod_path": "/exports/report.txt",
        "size_bytes": 6,
        "media_type": "text/plain",
    }


@pytest.mark.asyncio
async def test_the_callers_media_type_wins_over_the_one_the_pod_inferred():
    """The caller saw the response's content type; the pod guessed from the name."""

    async def write_file(uow, **kwargs):
        return StoredPodFile(
            pod_path="/exports/report.bin",
            size_bytes=6,
            media_type="application/octet-stream",
        )

    gateway = DatastorePodFileGateway("uow", write_file=write_file)

    written = await gateway.write_bytes(
        pod_id=uuid4(),
        directory="/exports",
        name="report.bin",
        content=b"report",
        media_type="application/pdf",
        ctx="ctx",
    )

    assert written["media_type"] == "application/pdf"
