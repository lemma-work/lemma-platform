from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.composition.connector_pod_files import DatastorePodFileGateway
from app.composition import pod_bundle_readme

pytestmark = pytest.mark.unit


class _FileService:
    async def download_file_content_by_path(self, pod_id, path, ctx):
        assert pod_id
        assert path == "/report.txt"
        assert ctx == "ctx"
        return SimpleNamespace(mime_type="text/plain", name="report.txt"), b"report"

    async def create_file(self, pod_id, name, content, ctx, *, directory_path):
        assert pod_id
        assert (name, content, ctx, directory_path) == (
            "report.txt",
            b"report",
            "ctx",
            "/exports",
        )
        return SimpleNamespace(path="/exports/report.txt", size_bytes=6, mime_type="text/plain")


@pytest.mark.asyncio
async def test_datastore_pod_file_gateway_uses_lazy_file_service(monkeypatch):
    service = _FileService()
    monkeypatch.setattr(
        "app.modules.datastore.api.dependencies.build_file_service",
        lambda uow: service,
    )
    gateway = DatastorePodFileGateway("uow")
    pod_id = uuid4()

    assert await gateway.read_bytes(pod_id=pod_id, path="/report.txt", ctx="ctx") == (
        b"report",
        "text/plain",
        "report.txt",
    )
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


def test_pod_bundle_readme_keeps_the_public_bindings():
    assert pod_bundle_readme.UsageExecutionContext
    assert pod_bundle_readme.AgentRuntimeProfileService
    assert "usage_limits_for" in pod_bundle_readme.__all__
