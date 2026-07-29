"""Agent Host-specific runtime profile creation and model selection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.modules.agent.domain.agent_host import (
    AgentHostHarnessHealth,
    AgentHostStatus,
    effective_agent_host_status,
    validate_agent_host_model,
    validate_agent_host_selections,
)
from app.modules.agent.domain.runtime_profiles import (
    HarnessRuntimeConfig,
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileType,
    RuntimeProfileScope,
    RuntimeProfileStatus,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostHarnessModel

if TYPE_CHECKING:
    from app.modules.agent.services.runtime_profile_service import (
        AgentRuntimeProfileService,
    )


async def create_harness_profile(
    service: AgentRuntimeProfileService,
    *,
    organization_id: UUID,
    user_id: UUID,
    harness_id: UUID,
    scope: RuntimeProfileScope,
    name: str,
    harness_snapshot_revision: str,
    config_selections: JsonObject,
    default_model_name: str | None = None,
    description: str | None = None,
    host_wait_timeout_seconds: int = 300,
    fallback_profile_id: str | None = None,
) -> AgentRuntimeProfile:
    if service.repository is None or service.host_repository is None:
        raise RuntimeError("Agent Host and runtime profile repositories are required")
    if scope not in {
        RuntimeProfileScope.ORGANIZATION,
        RuntimeProfileScope.PERSONAL,
    }:
        raise ValueError("Agent Host profile scope must be ORGANIZATION or PERSONAL")
    harness = await _require_ready_harness(
        host_repository=service.host_repository,
        harness_id=harness_id,
        harness_snapshot_revision=harness_snapshot_revision,
        organization_id=organization_id,
        user_id=user_id,
        scope=scope,
    )
    await validate_fallback_profile(
        service=service,
        fallback_profile_id=fallback_profile_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    selections = validate_agent_host_selections(
        config_options=harness.config_options or [],
        selections=config_selections,
    )
    selected_model = validate_agent_host_model(
        config_options=harness.config_options or [],
        model_name=default_model_name,
    )
    profile = AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=organization_id,
        owner_user_id=(
            user_id if scope is RuntimeProfileScope.PERSONAL else None
        ),
        harness_id=harness_id,
        scope=scope,
        runtime_type=RuntimeProfileType.HARNESS,
        name=_normalize_profile_name(name),
        description=description.strip() if description else None,
        default_model_name=selected_model,
        model_catalog=[],
        config=HarnessRuntimeConfig(
            harness_snapshot_revision=harness.config_revision,
            config_selections=selections,
            host_wait_timeout_seconds=host_wait_timeout_seconds,
            fallback_profile_id=fallback_profile_id,
        ),
        status=RuntimeProfileStatus.ACTIVE,
    )
    return await service.repository.create(profile)


async def _require_ready_harness(
    *,
    host_repository: AgentHostRepository,
    harness_id: UUID,
    harness_snapshot_revision: str,
    organization_id: UUID,
    user_id: UUID,
    scope: RuntimeProfileScope,
) -> AgentHostHarnessModel:
    harness = await host_repository.get_harness(harness_id=harness_id)
    if harness is None:
        raise ValueError("Agent Host harness is not available")
    host = await host_repository.get_for_user(
        host_id=harness.host_id,
        user_id=user_id,
    )
    if host is None or host.revoked_at is not None:
        raise ValueError("Agent Host harness is not owned by the current user")
    if (
        scope is RuntimeProfileScope.ORGANIZATION
        and host.organization_id != organization_id
    ):
        raise ValueError("Shared Agent Host profiles require an organization pairing")
    if host.organization_id not in {None, organization_id}:
        raise ValueError("Agent Host is paired to a different organization")
    if (
        effective_agent_host_status(host.status, host.last_seen_at)
        is not AgentHostStatus.ONLINE
    ):
        raise ValueError("Agent Host is offline or not accepting new runs")
    if harness.health != AgentHostHarnessHealth.READY.value:
        raise ValueError(f"Agent Host harness is not ready: {harness.health}")
    if harness.stale_after <= datetime.now(timezone.utc):
        raise ValueError("Agent Host harness snapshot is stale; refresh it")
    if harness.config_revision != harness_snapshot_revision:
        raise ValueError(
            "Agent Host harness changed; refresh configuration before saving"
        )
    return harness


async def validate_fallback_profile(
    *,
    service: AgentRuntimeProfileService,
    fallback_profile_id: str | None,
    organization_id: UUID,
    user_id: UUID,
) -> None:
    if fallback_profile_id is None:
        return
    fallback = await service.get_profile(
        profile_id=fallback_profile_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    if fallback is None:
        raise ValueError("Fallback runtime profile is not available")
    if fallback.status is not RuntimeProfileStatus.ACTIVE:
        raise ValueError("Fallback runtime profile is not active")
    if fallback.runtime_type is RuntimeProfileType.HARNESS:
        raise ValueError(
            "Fallback runtime profile cannot use a harness; "
            "fallback chains are intentionally unsupported"
        )


def selected_harness_model(
    profile: AgentRuntimeProfile,
    requested_model_name: str | None,
) -> RuntimeModelCatalogEntry | None:
    model_name = requested_model_name or profile.default_model_name
    if model_name is None:
        return None
    model_name = model_name.strip()
    if not model_name:
        return None
    return RuntimeModelCatalogEntry(
        name=model_name,
        display_name=model_name,
        provider_model_name=model_name,
        capabilities=[
            RuntimeModelCapability.TEXT,
            RuntimeModelCapability.TOOLS,
        ],
    )


def _config_dict(config: object | None) -> dict[str, object]:
    if config is None:
        return {}
    model_dump = getattr(config, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
        return value if isinstance(value, dict) else {}
    return config if isinstance(config, dict) else {}


def _normalize_profile_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Profile name cannot be empty")
    return normalized
