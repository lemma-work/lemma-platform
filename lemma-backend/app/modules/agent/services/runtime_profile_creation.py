"""Creating a runtime profile: discovery first, then the row.

Every creator here does the same two things in the same order, and the order is
the point. It talks to the provider -- an OpenAI-compatible `/models` endpoint,
an Anthropic-compatible one, or a paired Agent Host -- and only then writes,
because a profile whose catalog was never verified fails later at dispatch with
far less context about what went wrong.

The provider round trip happens under `connection_released`: the base URL came
from the caller, so the call is as slow as whatever answers it, and holding a
database connection open across it is how a handful of profile creations
exhaust the pool.

Split from `AgentRuntimeProfileService` because writing a profile and reading
one are different jobs with different failure modes; this side is the one that
can hang on somebody else's network.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import HttpUrl

from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent.domain.agent_host import (
    AgentHostHarnessHealth,
    AgentHostStatus,
    effective_agent_host_status,
)
from app.modules.agent.domain.agent_host_selections import (
    validate_agent_host_model,
    validate_agent_host_selections,
)
from app.modules.agent.domain.runtime_profiles import (
    AnthropicCompatibleRuntimeConfig,
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    HarnessRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.agent_host_repository import AgentHostRepository
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.services import runtime_provider_discovery as discovery
from app.modules.agent.services.runtime_system_profiles import (
    _agent_host_model_catalog,
)


def _normalize_profile_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Profile name cannot be empty")
    return normalized


def _normalized_headers(headers: dict[str, str] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (headers or {}).items():
        header_name = key.strip()
        header_value = value.strip()
        if header_name and header_value:
            normalized[header_name] = header_value
    return normalized


async def _create_profile(
    repository: AgentRuntimeProfileRepository,
    profile: AgentRuntimeProfile,
    *,
    name: str,
) -> AgentRuntimeProfile:
    del name
    return await repository.create(profile)


class RuntimeProfileCreation:
    """Writes a runtime profile, once the provider behind it has answered."""

    def __init__(
        self,
        repository: AgentRuntimeProfileRepository | None,
        host_repository: AgentHostRepository | None,
    ) -> None:
        self.repository = repository
        self.host_repository = host_repository

    def _session(self):
        """The session behind the repository, so the connection can be released.

        ``None`` when there is no repository (unit tests); `connection_released`
        treats that as nothing to release and passes straight through.
        """
        return getattr(getattr(self.repository, "uow", None), "session", None)

    async def create_agent_host_profile(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        harness_id: UUID,
        name: str,
        # See CreateAgentHostRuntimeProfileRequest: sharing one person's machine
        # with a whole workspace is never the thing a caller meant by saying
        # nothing.
        scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL,
        description: str | None = None,
        default_model_name: str | None = None,
        config_selections: JsonObject | None = None,
        host_wait_timeout_seconds: int = 300,
    ) -> AgentRuntimeProfile:
        """Bind a runtime profile to one harness on one paired Agent Host.

        The harness must be live and READY at save time: a profile written
        against an offline or unhealthy harness would only fail later, at
        dispatch, with far less context about what went wrong.
        """
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        if self.host_repository is None:
            raise RuntimeError("Agent Host repository is required")
        if scope not in {
            RuntimeProfileScope.ORGANIZATION,
            RuntimeProfileScope.PERSONAL,
        }:
            raise ValueError(
                "Agent Host profile scope must be ORGANIZATION or PERSONAL"
            )

        normalized_name = _normalize_profile_name(name)
        harness = await self.require_ready_harness(
            harness_id=harness_id,
            organization_id=organization_id,
            user_id=user_id,
            scope=scope,
        )
        config_options = list(harness.config_options or [])
        selections = validate_agent_host_selections(
            config_options=config_options,
            selections=config_selections or {},
        )
        selected_model = validate_agent_host_model(
            config_options=config_options,
            model_name=default_model_name,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            user_id=user_id if scope is RuntimeProfileScope.PERSONAL else None,
            harness_id=harness_id,
            scope=scope,
            kind=RuntimeProfileKind.HARNESS,
            protocol=RuntimeProfileProtocol.AGENT_HOST,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_model,
            model_catalog=_agent_host_model_catalog(
                config_options,
                # ``images`` is the one harness capability the server branches
                # on: it is what puts view_image in the run's toolset.
                supports_images=bool(
                    (harness.capabilities or {}).get("images") is True
                ),
            ),
            config=HarnessRuntimeConfig(
                harness_snapshot_revision=harness.config_revision,
                config_selections=selections,
                host_wait_timeout_seconds=host_wait_timeout_seconds,
            ),
            status=RuntimeProfileStatus.ACTIVE,
            metadata={
                "source": "AGENT_HOST",
                "harness_key": harness.harness_key,
            },
        )
        return await self.repository.create(profile)

    async def create_openai_compatible_profile(
        self,
        *,
        organization_id: UUID,
        name: str,
        base_url: str | HttpUrl,
        api_key: str | None = None,
        description: str | None = None,
        default_model_name: str | None = None,
        model_names: list[str] | None = None,
        vision_model_names: list[str] | None = None,
        headers: dict[str, str] | None = None,
        model_settings: dict[str, object] | None = None,
    ) -> AgentRuntimeProfile:
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        normalized_name = _normalize_profile_name(name)
        normalized_headers = _normalized_headers(headers)
        # Nothing has been read or written yet, so the caller's connection goes
        # back for the provider round trip -- an HTTP call to a base URL the
        # caller supplied, which is as slow as whatever answers it. The writes
        # below re-acquire.
        async with connection_released(self._session()):
            discovered_models = await discovery._discover_openai_compatible_models(
                base_url=str(base_url),
                api_key=api_key,
                headers=normalized_headers,
            )
        catalog = discovery._provider_model_catalog(
            discovered_models=discovered_models,
            fallback_model_names=model_names or [],
            explicit_vision_model_names={
                name.strip() for name in (vision_model_names or []) if name.strip()
            },
        )
        selected_default_model = discovery._select_provider_default_model(
            requested_model_name=default_model_name,
            catalog=catalog,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            scope=RuntimeProfileScope.ORGANIZATION,
            kind=RuntimeProfileKind.MODEL_PROVIDER,
            protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_default_model,
            model_catalog=catalog,
            config=OpenAICompatibleRuntimeConfig(
                base_url=base_url,
                headers=normalized_headers,
                model_settings=model_settings or {},
            ),
            credentials=(
                ApiKeyRuntimeCredentials(api_key=api_key.strip())
                if api_key and api_key.strip()
                else None
            ),
            status=RuntimeProfileStatus.ACTIVE,
            metadata={
                "source": "openai_compatible",
                "catalog_discovered": bool(discovered_models),
            },
        )
        return await _create_profile(self.repository, profile, name=normalized_name)

    async def create_anthropic_compatible_profile(
        self,
        *,
        organization_id: UUID,
        name: str,
        api_key: str,
        base_url: str | HttpUrl | None = None,
        description: str | None = None,
        default_model_name: str | None = None,
        model_names: list[str] | None = None,
        headers: dict[str, str] | None = None,
        model_settings: dict[str, object] | None = None,
    ) -> AgentRuntimeProfile:
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        normalized_name = _normalize_profile_name(name)
        normalized_headers = _normalized_headers(headers)
        # Nothing has been read or written yet, so the caller's connection goes
        # back for the provider round trip -- an HTTP call to a base URL the
        # caller supplied, which is as slow as whatever answers it. The writes
        # below re-acquire.
        async with connection_released(self._session()):
            discovered_models = await discovery._discover_anthropic_compatible_models(
                base_url=str(base_url or "https://api.anthropic.com"),
                api_key=api_key,
                headers=normalized_headers,
            )
        catalog = discovery._provider_model_catalog(
            discovered_models=discovered_models,
            fallback_model_names=model_names or [],
            # Anthropic/Claude models are uniformly multimodal, so every model in
            # an Anthropic-compatible profile keeps the vision tools.
            default_vision=True,
        )
        selected_default_model = discovery._select_provider_default_model(
            requested_model_name=default_model_name,
            catalog=catalog,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            scope=RuntimeProfileScope.ORGANIZATION,
            kind=RuntimeProfileKind.MODEL_PROVIDER,
            protocol=RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_default_model,
            model_catalog=catalog,
            config=AnthropicCompatibleRuntimeConfig(
                base_url=base_url,
                headers=normalized_headers,
                model_settings=model_settings or {},
            ),
            credentials=ApiKeyRuntimeCredentials(api_key=api_key.strip()),
            status=RuntimeProfileStatus.ACTIVE,
            metadata={
                "source": "anthropic_compatible",
                "catalog_discovered": bool(discovered_models),
            },
        )
        return await _create_profile(self.repository, profile, name=normalized_name)

    async def require_ready_harness(
        self,
        *,
        harness_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        scope: RuntimeProfileScope,
        require_owner: bool = True,
    ):
        assert self.host_repository is not None
        harness = await self.host_repository.get_harness(harness_id=harness_id)
        if harness is None:
            raise ValueError("Agent Host harness is not available")
        if require_owner:
            host = await self.host_repository.get_for_user(
                host_id=harness.host_id,
                user_id=user_id,
            )
        else:
            # Editing an already-bound shared profile is not binding a machine.
            # Requiring ownership here would stop an org admin from fixing a
            # profile a colleague created, while dispatch imposes no such check.
            host = await self.host_repository.get(host_id=harness.host_id)
        if host is None or host.revoked_at is not None:
            raise ValueError("Agent Host harness is not owned by the current user")
        # A paired computer belongs to the person who paired it, not to a
        # workspace: it runs on their machine, with their credentials. Sharing
        # it is the *profile's* decision - giving a profile ORGANIZATION scope
        # is the owner saying "my colleagues may send work here". The host
        # carries no organization at all, and dispatch already works this way:
        # it resolves a run through `harness.host_id` alone.
        if (
            effective_agent_host_status(host.status, host.last_seen_at)
            is not AgentHostStatus.ONLINE
        ):
            raise ValueError("Agent Host is offline or not accepting new runs")
        if harness.health != AgentHostHarnessHealth.READY.value:
            raise ValueError(f"Agent Host harness is not ready: {harness.health}")
        return harness
