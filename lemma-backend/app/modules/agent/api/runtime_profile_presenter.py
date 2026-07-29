"""Runtime-profile availability and live harness projections."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import TypeAdapter

from app.composition.identity_notifications import user_is_organization_member
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.api.schemas import AgentRuntimeProfileResponse
from app.modules.agent.domain.agent_host import (
    AgentHostHarnessHealth,
    AgentHostStatus,
    effective_agent_host_status,
    validate_agent_host_model,
    validate_agent_host_selections,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    HarnessRuntimeConfig,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileScope,
    RuntimeProfileType,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostHarnessModel,
    AgentHostModel,
)


_PROFILE_RESPONSE = TypeAdapter(AgentRuntimeProfileResponse)


async def profile_responses_with_runtime_status(
    profiles: list[AgentRuntimeProfile],
    *,
    user_id: UUID,
    uow: SqlAlchemyUnitOfWork,
) -> list[AgentRuntimeProfileResponse]:
    host_repo = AgentHostRepository(uow)
    responses: list[AgentRuntimeProfileResponse] = []
    for profile in profiles:
        payload = profile.public_dict()
        if profile.runtime_type is RuntimeProfileType.HARNESS:
            payload.update(
                await _harness_status_payload(
                    profile,
                    host_repo=host_repo,
                    user_id=user_id,
                    uow=uow,
                )
            )
        else:
            payload["availability_status"] = (
                "READY" if profile.status.value == "ACTIVE" else "DISABLED"
            )
        responses.append(_PROFILE_RESPONSE.validate_python(payload))
    return responses


async def _harness_status_payload(
    profile: AgentRuntimeProfile,
    *,
    host_repo: AgentHostRepository,
    user_id: UUID,
    uow: SqlAlchemyUnitOfWork,
) -> dict[str, object]:
    if profile.harness_id is None:
        return {"availability_status": "UNAVAILABLE"}
    harness = await host_repo.get_harness(harness_id=profile.harness_id)
    if harness is None:
        return {"availability_status": "UNAVAILABLE"}
    host = await _visible_host(
        profile,
        host_id=harness.host_id,
        host_repo=host_repo,
        user_id=user_id,
        uow=uow,
    )
    base: dict[str, object] = {
        "host_id": harness.host_id,
        "harness_key": harness.harness_key,
        "harness_health": harness.health,
        "harness_config_revision": harness.config_revision,
        "model_catalog": _harness_model_catalog(harness),
    }
    if host is None:
        base["availability_status"] = "UNAVAILABLE_FOR_YOU"
        return base

    host_status = effective_agent_host_status(host.status, host.last_seen_at)
    base.update(
        {
            "host_display_name": host.display_name,
            "host_status": host_status.value,
            "availability_status": _harness_availability(
                profile=profile,
                host=host,
                host_status=host_status,
                harness=harness,
            ),
        }
    )
    return base


async def _visible_host(
    profile: AgentRuntimeProfile,
    *,
    host_id: UUID,
    host_repo: AgentHostRepository,
    user_id: UUID,
    uow: SqlAlchemyUnitOfWork,
) -> AgentHostModel | None:
    if profile.scope is RuntimeProfileScope.PERSONAL:
        if profile.owner_user_id != user_id:
            return None
        return await host_repo.get_for_user(host_id=host_id, user_id=user_id)

    host = await host_repo.get(host_id)
    if host is None or host.organization_id != profile.organization_id:
        return None
    if profile.organization_id is None:
        return None
    if not await user_is_organization_member(
        uow,
        user_id=host.user_id,
        organization_id=profile.organization_id,
    ):
        return None
    return host


def _harness_availability(
    *,
    profile: AgentRuntimeProfile,
    host: AgentHostModel,
    host_status: AgentHostStatus,
    harness: AgentHostHarnessModel,
) -> str:
    if profile.status.value != "ACTIVE":
        return "DISABLED"
    if host.revoked_at is not None or host_status is AgentHostStatus.REVOKED:
        return "REVOKED"
    if host_status is not AgentHostStatus.ONLINE:
        return host_status.value
    if harness.health != AgentHostHarnessHealth.READY.value:
        return str(harness.health)
    if harness.stale_after <= datetime.now(timezone.utc):
        return "STALE"
    config = profile.config
    if not isinstance(config, HarnessRuntimeConfig):
        return "CONFIG_INVALID"
    if config.harness_snapshot_revision != harness.config_revision:
        return "CONFIG_REVISION_MISMATCH"
    try:
        validate_agent_host_selections(
            config_options=harness.config_options or [],
            selections=config.config_selections,
        )
        validate_agent_host_model(
            config_options=harness.config_options or [],
            model_name=profile.default_model_name,
        )
    except ValueError:
        return "CONFIG_INVALID"
    return "READY"


def _harness_model_catalog(
    harness: AgentHostHarnessModel,
) -> list[RuntimeModelCatalogEntry]:
    """Project the current harness model option into the runtime picker."""
    capabilities = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
    if isinstance(harness.capabilities, dict) and harness.capabilities.get("images"):
        capabilities.append(RuntimeModelCapability.VISION)
    for option in harness.config_options or []:
        if not isinstance(option, dict) or option.get("category") != "model":
            continue
        catalog: list[RuntimeModelCatalogEntry] = []
        for raw_value in option.get("options") or []:
            if not isinstance(raw_value, dict):
                continue
            value = raw_value.get("value", raw_value.get("id"))
            if not isinstance(value, str) or not value.strip():
                continue
            display_name = raw_value.get("name", raw_value.get("label"))
            catalog.append(
                RuntimeModelCatalogEntry(
                    name=value,
                    display_name=(
                        display_name.strip()
                        if isinstance(display_name, str) and display_name.strip()
                        else value
                    ),
                    provider_model_name=value,
                    capabilities=capabilities,
                )
            )
        return catalog
    return []
