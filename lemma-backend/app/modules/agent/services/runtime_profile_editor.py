"""Editing a runtime profile: update, archive, restore.

Separate from ``runtime_profile_service`` because creating a profile and
changing one have almost nothing in common. A create validates a blank slate; an
edit has to decide, field by field, whether the caller meant "leave this alone",
and for a harness whether the change is big enough to need the paired computer
awake.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import HttpUrl

from app.modules.agent.domain.agent_host_selections import (
    validate_agent_host_model,
    validate_agent_host_selections,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    AnthropicCompatibleRuntimeConfig,
    ApiKeyRuntimeCredentials,
    HarnessRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
    UNSET,
    _UnsetType,
    reveal_credentials,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
    _agent_host_model_catalog,
    _normalize_profile_name,
    _normalized_headers,
)
# Imported as a module so a patched discovery function is the one that runs.
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent.services import runtime_provider_discovery as discovery


def _harness_config(profile: AgentRuntimeProfile) -> HarnessRuntimeConfig:
    """The profile's harness config, tolerating a legacy raw-dict row."""
    if isinstance(profile.config, HarnessRuntimeConfig):
        return profile.config
    if isinstance(profile.config, dict):
        return HarnessRuntimeConfig.model_validate(profile.config)
    raise ValueError("This runtime profile has no Agent Host configuration")


def _provider_config(
    profile: AgentRuntimeProfile,
) -> OpenAICompatibleRuntimeConfig | AnthropicCompatibleRuntimeConfig:
    if isinstance(
        profile.config,
        (OpenAICompatibleRuntimeConfig, AnthropicCompatibleRuntimeConfig),
    ):
        return profile.config
    if isinstance(profile.config, dict):
        if profile.protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE:
            return AnthropicCompatibleRuntimeConfig.model_validate(profile.config)
        return OpenAICompatibleRuntimeConfig.model_validate(profile.config)
    raise ValueError("This runtime profile has no provider configuration")


class AgentRuntimeProfileEditor:
    """Changes to an existing profile, on behalf of one organization member."""

    def __init__(self, service: AgentRuntimeProfileService):
        self._service = service

    def _session(self):
        """The session behind the repository, so the connection can be released.

        Reached through the repository because the editor is constructed from a
        service, not a unit of work. Returns ``None`` when there is no
        repository (unit tests), which `connection_released` treats as "nothing
        to release" and passes straight through.
        """
        repository = self._service.repository
        return getattr(getattr(repository, "uow", None), "session", None)


    async def _load_editable(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
    ) -> AgentRuntimeProfile:
        if self._service.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        profile = await self._service.repository.get_visible_by_id(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            # An archived profile still has to be editable, or restoring one
            # into a name collision would leave no way to rename it out.
            include_disabled=True,
        )
        if profile is None:
            raise ValueError("Runtime profile is not available")
        if profile.scope is RuntimeProfileScope.SYSTEM:
            raise ValueError("The built-in Lemma profile cannot be edited")
        return profile

    async def update_agent_host_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
        name: str | _UnsetType = UNSET,
        description: str | None | _UnsetType = UNSET,
        default_model_name: str | None | _UnsetType = UNSET,
        config_selections: JsonObject | _UnsetType = UNSET,
        host_wait_timeout_seconds: int | _UnsetType = UNSET,
    ) -> AgentRuntimeProfile:
        """Edit a harness profile, contacting the harness only when needed.

        A rename must work while the paired computer is asleep, so only a change
        that touches the harness configuration requires it to be live.
        """
        assert self._service.repository is not None or True
        profile = await self._load_editable(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if profile.protocol is not RuntimeProfileProtocol.AGENT_HOST:
            raise ValueError("This runtime profile is not an Agent Host profile")
        assert self._service.repository is not None

        changes: dict[str, object] = {}
        if not isinstance(name, _UnsetType):
            changes["name"] = _normalize_profile_name(name)
        if not isinstance(description, _UnsetType):
            changes["description"] = description.strip() if description else None

        touches_configuration = not isinstance(
            default_model_name, _UnsetType
        ) or not isinstance(config_selections, _UnsetType)

        if touches_configuration:
            if self._service.host_repository is None:
                raise RuntimeError("Agent Host repository is required")
            if profile.harness_id is None:
                raise ValueError("This runtime profile is not bound to a harness")
            harness = await self._service.require_ready_harness(
                harness_id=profile.harness_id,
                organization_id=organization_id,
                user_id=user_id,
                scope=profile.scope,
                require_owner=profile.scope is not RuntimeProfileScope.ORGANIZATION,
            )
            config_options = list(harness.config_options or [])
            stored = _harness_config(profile)

            # Selections replace wholesale rather than merging: a key the
            # harness no longer advertises would otherwise fail validation on
            # every future edit with no way to drop it.
            selections = (
                stored.config_selections
                if isinstance(config_selections, _UnsetType)
                else config_selections
            )
            selections = validate_agent_host_selections(
                config_options=config_options,
                selections=selections or {},
            )

            catalog = _agent_host_model_catalog(
                config_options,
                supports_images=bool((harness.capabilities or {}).get("images") is True),
            )
            if isinstance(default_model_name, _UnsetType):
                # The caller did not ask to change the model, so a stored one
                # that the harness has since dropped is cleared rather than
                # failing an unrelated edit. An unpinned harness profile is
                # legal - the harness picks its own default.
                catalog_names = {entry.name for entry in catalog}
                selected_model = (
                    profile.default_model_name
                    if profile.default_model_name in catalog_names
                    else None
                )
            else:
                selected_model = validate_agent_host_model(
                    config_options=config_options,
                    model_name=default_model_name,
                )

            changes["default_model_name"] = selected_model
            changes["model_catalog"] = catalog
            changes["config"] = HarnessRuntimeConfig(
                # Re-pin to what was just validated. Without this the next
                # dispatch rejects the run as "harness configuration changed
                # after profile validation".
                harness_snapshot_revision=harness.config_revision,
                config_selections=selections,
                host_wait_timeout_seconds=(
                    stored.host_wait_timeout_seconds
                    if isinstance(host_wait_timeout_seconds, _UnsetType)
                    else host_wait_timeout_seconds
                ),
            )
        elif not isinstance(host_wait_timeout_seconds, _UnsetType):
            stored = _harness_config(profile)
            changes["config"] = stored.model_copy(
                update={"host_wait_timeout_seconds": host_wait_timeout_seconds}
            )

        if not changes:
            return profile
        return await self._service.repository.update(profile.with_changes(**changes))

    async def update_openai_compatible_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
        name: str | _UnsetType = UNSET,
        description: str | None | _UnsetType = UNSET,
        base_url: str | HttpUrl | _UnsetType = UNSET,
        api_key: str | None | _UnsetType = UNSET,
        default_model_name: str | None | _UnsetType = UNSET,
        model_names: list[str] | _UnsetType = UNSET,
        headers: dict[str, str] | _UnsetType = UNSET,
        model_settings: dict[str, object] | _UnsetType = UNSET,
        refresh_models: bool = False,
    ) -> AgentRuntimeProfile:
        return await self._update_provider(
            protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            name=name,
            description=description,
            base_url=base_url,
            api_key=api_key,
            default_model_name=default_model_name,
            model_names=model_names,
            headers=headers,
            model_settings=model_settings,
            refresh_models=refresh_models,
        )

    async def update_anthropic_compatible_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
        name: str | _UnsetType = UNSET,
        description: str | None | _UnsetType = UNSET,
        base_url: str | HttpUrl | None | _UnsetType = UNSET,
        api_key: str | _UnsetType = UNSET,
        default_model_name: str | None | _UnsetType = UNSET,
        model_names: list[str] | _UnsetType = UNSET,
        headers: dict[str, str] | _UnsetType = UNSET,
        model_settings: dict[str, object] | _UnsetType = UNSET,
        refresh_models: bool = False,
    ) -> AgentRuntimeProfile:
        return await self._update_provider(
            protocol=RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            name=name,
            description=description,
            base_url=base_url,
            api_key=api_key,
            default_model_name=default_model_name,
            model_names=model_names,
            headers=headers,
            model_settings=model_settings,
            refresh_models=refresh_models,
        )

    async def _update_provider(
        self,
        *,
        protocol: RuntimeProfileProtocol,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
        name: str | _UnsetType,
        description: str | None | _UnsetType,
        base_url: str | HttpUrl | None | _UnsetType,
        api_key: str | None | _UnsetType,
        default_model_name: str | None | _UnsetType,
        model_names: list[str] | _UnsetType,
        headers: dict[str, str] | _UnsetType,
        model_settings: dict[str, object] | _UnsetType,
        refresh_models: bool,
    ) -> AgentRuntimeProfile:
        profile = await self._load_editable(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if profile.protocol is not protocol:
            raise ValueError("This runtime profile has a different provider protocol")
        assert self._service.repository is not None
        is_anthropic = protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE
        stored = _provider_config(profile)

        changes: dict[str, object] = {}
        if not isinstance(name, _UnsetType):
            changes["name"] = _normalize_profile_name(name)
        if not isinstance(description, _UnsetType):
            changes["description"] = description.strip() if description else None

        if isinstance(base_url, _UnsetType):
            next_base_url = stored.base_url
        elif base_url is None:
            if not is_anthropic:
                raise ValueError("An OpenAI-compatible profile requires a base URL")
            next_base_url = None
        else:
            next_base_url = base_url
        base_url_changed = not isinstance(base_url, _UnsetType) and str(
            next_base_url
        ) != str(stored.base_url)

        if base_url_changed and next_base_url is not None:
            # Validate directly rather than relying on discovery to do it: an
            # edit that changes the URL without re-discovering would otherwise
            # never see the SSRF guard at all. Released for the same reason as
            # the discovery below -- the guard resolves DNS, so it is a network
            # call however quick it usually is.
            async with connection_released(self._session()):
                await discovery._validate_public_base_url(str(next_base_url))

        next_headers = (
            stored.headers if isinstance(headers, _UnsetType) else _normalized_headers(headers)
        )
        next_settings = (
            stored.model_settings
            if isinstance(model_settings, _UnsetType)
            else (model_settings or {})
        )

        # Credentials have three states: absent keeps, a string rotates, an
        # explicit null clears.
        next_credentials = profile.credentials
        if not isinstance(api_key, _UnsetType):
            if api_key is None or not str(api_key).strip():
                if is_anthropic:
                    raise ValueError("An Anthropic-compatible profile requires an API key")
                next_credentials = None
            else:
                next_credentials = ApiKeyRuntimeCredentials(api_key=str(api_key).strip())
        if not isinstance(api_key, _UnsetType) or next_credentials is not profile.credentials:
            changes["credentials"] = next_credentials

        rediscover = (
            refresh_models
            or base_url_changed
            or not isinstance(api_key, _UnsetType)
            or not isinstance(model_names, _UnsetType)
        )
        if rediscover:
            revealed = reveal_credentials(next_credentials) or {}
            secret = revealed.get("api_key")
            discovery_url = str(next_base_url or "https://api.anthropic.com")
            # The profile was read above and nothing has been written yet, so
            # the connection can go back for the length of the provider call --
            # an HTTP round trip to a caller-supplied base URL, which is as slow
            # as whatever is at the other end. The write below re-acquires.
            async with connection_released(self._session()):
                discovered = (
                    await discovery._discover_anthropic_compatible_models(
                        base_url=discovery_url,
                        api_key=str(secret or ""),
                        headers=next_headers,
                    )
                    if is_anthropic
                    else await discovery._discover_openai_compatible_models(
                        base_url=discovery_url,
                        api_key=str(secret) if secret else None,
                        headers=next_headers,
                    )
                )
            if not isinstance(model_names, _UnsetType):
                fallback_names = list(model_names)
            elif discovered:
                # _provider_model_catalog unions its fallback with what it
                # discovered, so passing the stored names here would keep a
                # model the provider has dropped selectable forever.
                fallback_names = []
            else:
                # Discovery came back empty - a brief outage, or a provider with
                # no /models endpoint. Keep the working catalog rather than
                # blanking it.
                fallback_names = [entry.name for entry in profile.model_catalog]
            catalog = discovery._provider_model_catalog(
                discovered_models=discovered,
                fallback_model_names=fallback_names,
                default_vision=is_anthropic,
            )
            changes["model_catalog"] = catalog
            changes["metadata"] = {
                **profile.metadata,
                "catalog_discovered": bool(discovered),
            }
        else:
            catalog = list(profile.model_catalog)

        catalog_names = {entry.name for entry in catalog}
        if not isinstance(default_model_name, _UnsetType):
            changes["default_model_name"] = discovery._select_provider_default_model(
                requested_model_name=default_model_name,
                catalog=catalog,
            )
        elif catalog and profile.default_model_name not in catalog_names:
            # The provider dropped the pinned model. Following it is better than
            # failing an edit the user did not connect to that model.
            changes["default_model_name"] = catalog[0].name

        if base_url_changed or not isinstance(headers, _UnsetType) or not isinstance(
            model_settings, _UnsetType
        ):
            config_type = (
                AnthropicCompatibleRuntimeConfig
                if is_anthropic
                else OpenAICompatibleRuntimeConfig
            )
            changes["config"] = config_type(
                base_url=next_base_url,
                headers=next_headers,
                model_settings=next_settings,
            )

        if not changes:
            return profile
        return await self._service.repository.update(profile.with_changes(**changes))

    async def archive_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
    ) -> AgentRuntimeProfile:
        """Retire a profile without deleting it.

        Five places store a bare profile id with no foreign key - agents,
        conversations, runs, pod config, usage records - and a run lease's
        profile pointer is ON DELETE SET NULL, which would break the dispatch
        idempotency check for an in-flight run. Archiving keeps all of that
        resolvable and is reversible.
        """
        return await self._set_status(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            status=RuntimeProfileStatus.DISABLED,
        )

    async def restore_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
    ) -> AgentRuntimeProfile:
        return await self._set_status(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            status=RuntimeProfileStatus.ACTIVE,
        )

    async def _set_status(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
        status: RuntimeProfileStatus,
    ) -> AgentRuntimeProfile:
        profile = await self._load_editable(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        assert self._service.repository is not None
        if profile.status is status:
            return profile
        updated = await self._service.repository.set_status(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            status=status,
        )
        if updated is None:
            raise ValueError("Runtime profile is not available")
        return updated
