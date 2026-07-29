"""Agent Host-specific runtime profile creation and model selection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.modules.agent.domain.agent_host import (
    FOLLOW_ADAPTER_DEFAULT,
    AgentHostIntegrationHealth,
    AgentHostStatus,
    effective_agent_host_status,
    validate_agent_host_selections,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentHostRuntimeConfig,
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostIntegrationModel

if TYPE_CHECKING:
    from app.modules.agent.services.runtime_profile_service import (
        AgentRuntimeProfileService,
    )


async def create_agent_host_profile(
    service: AgentRuntimeProfileService,
    *,
    organization_id: UUID,
    user_id: UUID,
    host_integration_id: UUID,
    scope: RuntimeProfileScope,
    name: str,
    integration_snapshot_revision: str,
    config_selections: JsonObject,
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
    integration = await _require_ready_integration(
        host_repository=service.host_repository,
        integration_id=host_integration_id,
        integration_snapshot_revision=integration_snapshot_revision,
        organization_id=organization_id,
        user_id=user_id,
        scope=scope,
    )
    await _validate_fallback(
        service=service,
        fallback_profile_id=fallback_profile_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    selections = validate_agent_host_selections(
        config_options=integration.config_options or [],
        selections=config_selections,
    )
    selected_model = selections.get("model")
    profile = AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=organization_id,
        user_id=user_id,
        host_integration_id=host_integration_id,
        scope=scope,
        kind=RuntimeProfileKind.EXTERNAL_AGENT,
        protocol=RuntimeProfileProtocol.AGENT_HOST_V2,
        name=_normalize_profile_name(name),
        description=description.strip() if description else None,
        default_model_name=(
            selected_model
            if isinstance(selected_model, str)
            and selected_model != FOLLOW_ADAPTER_DEFAULT
            else FOLLOW_ADAPTER_DEFAULT
        ),
        model_catalog=[],
        config=AgentHostRuntimeConfig(
            integration_snapshot_revision=integration.config_revision,
            config_selections=selections,
            host_wait_timeout_seconds=host_wait_timeout_seconds,
            fallback_profile_id=fallback_profile_id,
        ),
        status=RuntimeProfileStatus.ACTIVE,
        metadata={
            "source": "AGENT_HOST",
            "integration_key": integration.integration_key,
        },
    )
    return await service.repository.create(profile)


async def _require_ready_integration(
    *,
    host_repository: AgentHostRepository,
    integration_id: UUID,
    integration_snapshot_revision: str,
    organization_id: UUID,
    user_id: UUID,
    scope: RuntimeProfileScope,
) -> AgentHostIntegrationModel:
    integration = await host_repository.get_integration(integration_id=integration_id)
    if integration is None:
        raise ValueError("Agent Host integration is not available")
    host = await host_repository.get_for_user(
        host_id=integration.host_id,
        user_id=user_id,
    )
    if host is None or host.revoked_at is not None:
        raise ValueError("Agent Host integration is not owned by the current user")
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
    if integration.health != AgentHostIntegrationHealth.READY.value:
        raise ValueError(f"Agent Host integration is not ready: {integration.health}")
    if integration.stale_after <= datetime.now(timezone.utc):
        raise ValueError("Agent Host integration snapshot is stale; refresh it")
    if integration.config_revision != integration_snapshot_revision:
        raise ValueError(
            "Agent Host integration changed; refresh configuration before saving"
        )
    return integration


async def _validate_fallback(
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
    if fallback.protocol is RuntimeProfileProtocol.AGENT_HOST_V2:
        raise ValueError(
            "Fallback runtime profile cannot use Agent Host; "
            "fallback chains are intentionally unsupported"
        )


def selected_agent_host_model(
    profile: AgentRuntimeProfile,
    requested_model_name: str | None,
) -> RuntimeModelCatalogEntry | None:
    config = _config_dict(profile.config)
    selections = config.get("config_selections")
    configured_model = selections.get("model") if isinstance(selections, dict) else None
    model_name = requested_model_name or configured_model or FOLLOW_ADAPTER_DEFAULT
    if not isinstance(model_name, str) or not model_name.strip():
        return None
    return RuntimeModelCatalogEntry(
        name=model_name,
        display_name=(
            "Adapter default" if model_name == FOLLOW_ADAPTER_DEFAULT else model_name
        ),
        provider_model_name=model_name,
        capabilities=[
            RuntimeModelCapability.TEXT,
            RuntimeModelCapability.TOOLS,
        ],
        metadata={"dynamic_agent_host_selection": True},
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
