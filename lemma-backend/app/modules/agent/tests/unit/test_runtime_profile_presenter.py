from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.api import runtime_profile_presenter as presenter
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    HarnessRuntimeConfig,
    RuntimeProfileScope,
    RuntimeProfileType,
)


def profile(*, organization_id, owner_user_id, scope, harness_id=None):
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        harness_id=harness_id or uuid4(),
        scope=scope,
        runtime_type=RuntimeProfileType.HARNESS,
        name="Local Codex",
        default_model_name=None,
        config=HarnessRuntimeConfig(
            harness_snapshot_revision="revision-1",
            config_selections={"mode": "safe"},
        ),
    )


@pytest.mark.asyncio
async def test_workspace_profile_exposes_owner_host_to_other_member(
    monkeypatch,
) -> None:
    organization_id = uuid4()
    owner_user_id = uuid4()
    host = SimpleNamespace(
        id=uuid4(),
        user_id=owner_user_id,
        organization_id=organization_id,
    )

    async def is_member(*_args, **_kwargs):
        return True

    monkeypatch.setattr(presenter, "user_is_organization_member", is_member)
    visible = await presenter._visible_host(
        profile(
            organization_id=organization_id,
            owner_user_id=None,
            scope=RuntimeProfileScope.ORGANIZATION,
        ),
        host=host,
        membership_cache={},
        user_id=uuid4(),
        uow=SimpleNamespace(),
    )
    assert visible is host


@pytest.mark.asyncio
async def test_workspace_profile_is_unavailable_after_owner_membership_removal(
    monkeypatch,
) -> None:
    organization_id = uuid4()
    host = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=organization_id,
    )

    async def is_member(*_args, **_kwargs):
        return False

    monkeypatch.setattr(presenter, "user_is_organization_member", is_member)
    visible = await presenter._visible_host(
        profile(
            organization_id=organization_id,
            owner_user_id=None,
            scope=RuntimeProfileScope.ORGANIZATION,
        ),
        host=host,
        membership_cache={},
        user_id=uuid4(),
        uow=SimpleNamespace(),
    )
    assert visible is None


@pytest.mark.asyncio
async def test_personal_profile_hides_host_from_other_member() -> None:
    organization_id = uuid4()
    owner_user_id = uuid4()
    host = SimpleNamespace(
        id=uuid4(),
        user_id=owner_user_id,
        organization_id=organization_id,
    )
    visible = await presenter._visible_host(
        profile(
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            scope=RuntimeProfileScope.PERSONAL,
        ),
        host=host,
        membership_cache={},
        user_id=uuid4(),
        uow=SimpleNamespace(),
    )
    assert visible is None


def test_harness_availability_covers_host_and_revision_boundaries():
    now = datetime.now(timezone.utc)
    organization_id = uuid4()
    runtime_profile = profile(
        organization_id=organization_id,
        owner_user_id=None,
        scope=RuntimeProfileScope.ORGANIZATION,
    )
    host = SimpleNamespace(revoked_at=None)
    harness = SimpleNamespace(
        health="READY",
        stale_after=now + timedelta(hours=1),
        config_revision="revision-1",
        config_options=[
            {
                "id": "mode",
                "category": "mode",
                "required": True,
                "options": [{"value": "safe"}],
            }
        ],
    )
    assert (
        presenter._harness_availability(
            profile=runtime_profile,
            host=host,
            host_status=presenter.AgentHostStatus.ONLINE,
            harness=harness,
        )
        == "READY"
    )
    harness.config_revision = "revision-2"
    assert (
        presenter._harness_availability(
            profile=runtime_profile,
            host=host,
            host_status=presenter.AgentHostStatus.ONLINE,
            harness=harness,
        )
        == "CONFIG_REVISION_MISMATCH"
    )
    harness.config_revision = "revision-1"
    harness.stale_after = now - timedelta(seconds=1)
    assert (
        presenter._harness_availability(
            profile=runtime_profile,
            host=host,
            host_status=presenter.AgentHostStatus.ONLINE,
            harness=harness,
        )
        == "STALE"
    )
