"""Deleting a function must revoke its in-flight delegated tokens."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.function.services import function_service as function_service_module
from app.modules.function.services.function_service import FunctionService


def _service() -> FunctionService:
    return FunctionService(
        function_repository=AsyncMock(),
        run_repository=AsyncMock(),
        storage_factory=AsyncMock(),
        icon_service=None,
    )


@pytest.mark.asyncio
async def test_delete_function_revokes_delegation(monkeypatch):
    function = SimpleNamespace(id=uuid4(), pod_id=uuid4(), icon_url=None)
    service = _service()
    monkeypatch.setattr(
        service, "_load_function_by_name", AsyncMock(return_value=function)
    )
    monkeypatch.setattr(service, "_delete_function_row", AsyncMock(return_value=True))
    revoke_spy = AsyncMock()
    monkeypatch.setattr(function_service_module, "revoke_delegation", revoke_spy)

    ctx = SimpleNamespace(require=AsyncMock())
    result = await service.delete_function(uuid4(), "reporter", uuid4(), ctx=ctx)

    assert result is True
    revoke_spy.assert_awaited_once_with(actor_id=function.id)
