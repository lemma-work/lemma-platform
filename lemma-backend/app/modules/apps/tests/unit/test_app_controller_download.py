"""Use-case-level regression for DB pool exhaustion.

The app archive downloads must resolve + authorize the archive location *inside*
a short Unit of Work and read the archive bytes from storage *after* that UoW
(and its pooled DB connection) has been released. These tests drive
``AppUseCases`` (the owner of the saga) with a tracking ``uow_factory`` to pin
that ordering.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.apps.application.app_use_cases import AppUseCases


class _TrackingUowFactory:
    def __init__(self):
        self.state = {"open": False, "opens": 0}

    def __call__(self):
        state = self.state

        class _Cm:
            async def __aenter__(self_):
                state["open"] = True
                state["opens"] += 1
                return SimpleNamespace(session=object())

            async def __aexit__(self_, *exc):
                state["open"] = False
                return False

        return _Cm()


class _FakeAppService:
    def __init__(self, state, content):
        self._state = state
        self._content = content
        self.resolved_while_open = None
        self.read_while_open = None

    async def resolve_source_archive(self, pod_id, name, user_id, ctx=None):
        self.resolved_while_open = self._state["open"]
        return uuid4(), "source/archive.zip"

    async def resolve_dist_archive(self, pod_id, name, user_id, ctx=None):
        self.resolved_while_open = self._state["open"]
        return uuid4(), "releases/v1/dist/archive.zip"

    async def read_archive(self, app_id, archive_path):
        self.read_while_open = self._state["open"]
        return self._content


@pytest.fixture(autouse=True)
def _stub_pod_context(monkeypatch):
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=object()),
    )


def _use_cases(factory, service):
    return AppUseCases(factory, lambda uow: service)


@pytest.mark.asyncio
async def test_source_archive_resolves_in_uow_then_reads_after_release():
    factory = _TrackingUowFactory()
    service = _FakeAppService(factory.state, b"SOURCE-ZIP")

    archive = await _use_cases(factory, service).download_source_archive(
        pod_id=uuid4(), app_name="dashboard", request=SimpleNamespace(), user_id=uuid4()
    )

    assert service.resolved_while_open is True
    assert service.read_while_open is False
    assert factory.state["open"] is False
    assert factory.state["opens"] == 1
    assert archive == b"SOURCE-ZIP"


@pytest.mark.asyncio
async def test_dist_archive_resolves_in_uow_then_reads_after_release():
    factory = _TrackingUowFactory()
    service = _FakeAppService(factory.state, b"DIST-ZIP")

    archive = await _use_cases(factory, service).download_dist_archive(
        pod_id=uuid4(), app_name="dashboard", request=SimpleNamespace(), user_id=uuid4()
    )

    assert service.resolved_while_open is True
    assert service.read_while_open is False
    assert factory.state["open"] is False
    assert archive == b"DIST-ZIP"


@pytest.mark.asyncio
async def test_bundle_finalize_failure_cleans_storage_before_reraising():
    factory = _TrackingUowFactory()
    plan = object()
    written = object()

    class _FailingFinalizeService:
        async def resolve_upload_bundle(self, *args, **kwargs):
            return plan

        async def write_bundle_storage(self, *args, **kwargs):
            return written

        async def finalize_upload_bundle(self, *args, **kwargs):
            raise RuntimeError("database commit failed")

        cleanup_written_bundle = AsyncMock()

    service = _FailingFinalizeService()

    with pytest.raises(RuntimeError, match="database commit failed"):
        await _use_cases(factory, service).upload_bundle(
            pod_id=uuid4(),
            app_name="dashboard",
            request=SimpleNamespace(),
            user_id=uuid4(),
            source_archive_bytes=None,
            dist_archive_bytes=b"dist archive placeholder",
        )

    service.cleanup_written_bundle.assert_awaited_once_with(plan, written)
    assert factory.state["open"] is False
    assert factory.state["opens"] == 2
