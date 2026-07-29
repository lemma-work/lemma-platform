"""Runtime-profile status and model-catalog projections."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.api.schemas import AgentRuntimeProfileResponse
from app.modules.agent.domain.agent_host import (
    AgentHostIntegrationHealth,
    AgentHostStatus,
    effective_agent_host_status,
    validate_agent_host_selections,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileScope,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.repositories import AgentRuntimeDaemonRepository
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostIntegrationModel,
    AgentHostModel,
)


async def profile_responses_with_runtime_status(
    profiles: list[AgentRuntimeProfile],
    *,
    user_id: UUID,
    uow: SqlAlchemyUnitOfWork,
) -> list[AgentRuntimeProfileResponse]:
    daemon_repo = AgentRuntimeDaemonRepository(uow)
    responses: list[AgentRuntimeProfileResponse] = []
    for profile in profiles:
        payload = profile.public_dict()
        if profile.daemon_id is not None:
            payload.update(
                await _daemon_status_payload(
                    profile,
                    daemon_repo=daemon_repo,
                    user_id=user_id,
                )
            )
        if profile.host_integration_id is not None:
            payload.update(
                await _agent_host_status_payload(
                    profile,
                    host_repo=AgentHostRepository(uow),
                    user_id=user_id,
                )
            )
        responses.append(AgentRuntimeProfileResponse.model_validate(payload))
    return responses


async def _daemon_status_payload(
    profile: AgentRuntimeProfile,
    *,
    daemon_repo: AgentRuntimeDaemonRepository,
    user_id: UUID,
) -> dict[str, object]:
    if profile.user_id is None or profile.daemon_id is None:
        return {
            "daemon_harness_available": False,
            "availability_status": "UNAVAILABLE",
        }
    daemon = await daemon_repo.get_for_user(
        daemon_id=profile.daemon_id,
        user_id=profile.user_id,
    )
    if daemon is None:
        return {
            "daemon_harness_available": False,
            "availability_status": "UNAVAILABLE",
        }
    catalog = _json_object(getattr(daemon, "harness_catalog", None))
    raw_info = catalog.get(profile.derived_harness_kind().value)
    harness_available = (
        isinstance(raw_info, dict) and raw_info.get("available") is not False
    )
    if profile.scope is RuntimeProfileScope.PERSONAL and profile.user_id != user_id:
        availability_status = "UNAVAILABLE_FOR_YOU"
    elif daemon.status != "ONLINE":
        availability_status = "OFFLINE"
    elif not harness_available:
        availability_status = "NOT_INSTALLED"
    else:
        availability_status = "READY"
    return {
        "daemon_display_name": daemon.display_name,
        "daemon_status": daemon.status,
        "daemon_harness_available": harness_available,
        "availability_status": availability_status,
    }


async def _agent_host_status_payload(
    profile: AgentRuntimeProfile,
    *,
    host_repo: AgentHostRepository,
    user_id: UUID,
) -> dict[str, object]:
    if profile.host_integration_id is None:
        return {"availability_status": "UNAVAILABLE"}
    integration = await host_repo.get_integration(
        integration_id=profile.host_integration_id
    )
    if integration is None:
        return {"availability_status": "UNAVAILABLE"}
    host = await _visible_agent_host(
        profile,
        host_id=integration.host_id,
        host_repo=host_repo,
        user_id=user_id,
    )
    if host is None:
        return {
            "host_id": integration.host_id,
            "integration_key": integration.integration_key,
            "integration_health": integration.health,
            "integration_config_revision": integration.config_revision,
            "availability_status": "UNAVAILABLE_FOR_YOU",
        }
    config = _profile_config(profile.config)
    host_status = effective_agent_host_status(host.status, host.last_seen_at)
    availability = _agent_host_availability(
        host=host,
        host_status=host_status,
        integration=integration,
        config=config,
    )
    return {
        "host_id": host.id,
        "host_display_name": host.display_name,
        "host_status": host_status.value,
        "integration_key": integration.integration_key,
        "integration_health": integration.health,
        "integration_config_revision": integration.config_revision,
        "model_catalog": _agent_host_model_catalog(integration),
        "availability_status": availability,
    }


async def _visible_agent_host(
    profile: AgentRuntimeProfile,
    *,
    host_id: UUID,
    host_repo: AgentHostRepository,
    user_id: UUID,
) -> AgentHostModel | None:
    if profile.scope is RuntimeProfileScope.PERSONAL:
        if profile.user_id != user_id:
            return None
        return await host_repo.get_for_user(host_id=host_id, user_id=user_id)
    host = await host_repo.get(host_id)
    if (
        host is None
        or host.user_id != profile.user_id
        or host.organization_id != profile.organization_id
    ):
        return None
    return host


def _agent_host_availability(
    *,
    host: AgentHostModel,
    host_status: AgentHostStatus,
    integration: AgentHostIntegrationModel,
    config: object,
) -> str:
    if host.revoked_at is not None or host_status is AgentHostStatus.REVOKED:
        return "REVOKED"
    if host_status is not AgentHostStatus.ONLINE:
        return host_status.value
    if integration.health != AgentHostIntegrationHealth.READY.value:
        return str(integration.health)
    if integration.stale_after <= datetime.now(timezone.utc):
        return "STALE"
    selections = config.get("config_selections") if isinstance(config, dict) else {}
    try:
        validate_agent_host_selections(
            config_options=integration.config_options or [],
            selections=selections if isinstance(selections, dict) else {},
        )
    except ValueError:
        return "CONFIG_INVALID"
    return "READY"


def _agent_host_model_catalog(
    integration: object,
) -> list[RuntimeModelCatalogEntry]:
    """Project the provider-owned live model option into the generic picker."""
    capabilities = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
    raw_capabilities = getattr(integration, "capabilities", None)
    if isinstance(raw_capabilities, dict) and raw_capabilities.get("images") is True:
        capabilities.append(RuntimeModelCapability.VISION)
    for option in getattr(integration, "config_options", None) or []:
        if not isinstance(option, dict):
            continue
        if option.get("category") != "model" and option.get("id") != "model":
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
                    metadata={
                        "dynamic_agent_host_selection": True,
                        "config_revision": getattr(integration, "config_revision", ""),
                    },
                )
            )
        return catalog
    return []


def _json_object(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _profile_config(value: object) -> dict[str, object]:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_object(model_dump(mode="json"))
    return _json_object(value)
