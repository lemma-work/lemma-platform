from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.context import ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.agent.api.controllers import runtime_config_controller as controller
from app.modules.agent.api.schemas import (
    CreateOpenAICompatibleRuntimeProfileRequest,
    CreateUserDaemonRuntimeProfileRequest,
)
from app.modules.agent.domain.runtime_profiles import RuntimeProfileScope
from app.modules.agent.domain.value_objects import HarnessKind


@pytest.mark.asyncio
async def test_personal_daemon_profile_requires_membership_not_org_update(monkeypatch):
    ensure_member = AsyncMock()
    monkeypatch.setattr(controller, "_ensure_org_member", ensure_member)
    user = SimpleNamespace(id=uuid4())
    uow = object()
    ctx = SimpleNamespace(require=AsyncMock())
    org_id = uuid4()

    await controller._authorize_runtime_profile_create(
        org_id=org_id,
        data=CreateUserDaemonRuntimeProfileRequest(
            daemon_id=uuid4(),
            harness_kind=HarnessKind.GG_CODER,
            scope=RuntimeProfileScope.PERSONAL,
            name="My GG Coder",
        ),
        user=user,
        uow=uow,
        ctx=ctx,
    )

    ensure_member.assert_awaited_once_with(org_id=org_id, user=user, uow=uow)
    ctx.require.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["workspace_daemon", "provider"])
async def test_org_wide_runtime_profile_requires_org_update(monkeypatch, source):
    ensure_member = AsyncMock()
    monkeypatch.setattr(controller, "_ensure_org_member", ensure_member)
    ctx = SimpleNamespace(require=AsyncMock())
    org_id = uuid4()
    if source == "workspace_daemon":
        data = CreateUserDaemonRuntimeProfileRequest(
            daemon_id=uuid4(),
            harness_kind=HarnessKind.GG_CODER,
            scope=RuntimeProfileScope.ORGANIZATION,
            name="Workspace GG Coder",
        )
    else:
        data = CreateOpenAICompatibleRuntimeProfileRequest(
            name="Provider",
            base_url="https://provider.test/v1",
            model_names=["model"],
        )

    await controller._authorize_runtime_profile_create(
        org_id=org_id,
        data=data,
        user=SimpleNamespace(id=uuid4()),
        uow=object(),
        ctx=ctx,
    )

    ctx.require.assert_awaited_once_with(
        Permissions.ORG_UPDATE,
        ResourceRef.organization(org_id),
    )
    ensure_member.assert_not_awaited()
