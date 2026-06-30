import pytest
from uuid import uuid4
from types import SimpleNamespace

from app.modules.agent.defaults import default_agent_runtime_profile_id
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    HarnessKind,
    HarnessOptions,
)
from app.modules.agent.agent_runtime_defaults import (
    AgentRuntimeDefaultError,
    AgentRuntimeDefaultService,
)
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    AgentRuntimeProfileService,
    DiscoveredModel,
    _selected_model,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai import (
    _runtime_profile_model,
)


def _test_profile(
    *,
    scope: RuntimeProfileScope,
    organization_id=None,
    user_id=None,
    name: str,
) -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=organization_id,
        user_id=user_id,
        scope=scope,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name=name,
        default_model_name="default",
        model_catalog=[
            RuntimeModelCatalogEntry(
                name="default",
                provider_model_name=f"provider/{name}",
            ),
            RuntimeModelCatalogEntry(
                name="deepseek-v4-pro",
                provider_model_name=f"provider/{name}/deepseek",
            ),
        ],
        config={"base_url": "https://provider.test/v1"},
    )


def _test_harness_profile(
    *,
    organization_id,
    name: str,
    protocol: RuntimeProfileProtocol,
) -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=organization_id,
        scope=RuntimeProfileScope.ORGANIZATION,
        kind=RuntimeProfileKind.HARNESS,
        protocol=protocol,
        name=name,
        default_model_name="default",
        model_catalog=[
            RuntimeModelCatalogEntry(
                name="default",
                provider_model_name="default",
            )
        ],
        config={"binary": name},
    )


class _ProfileRepository:
    def __init__(self, profiles: list[AgentRuntimeProfile]):
        self.profiles = profiles

    async def get_visible(
        self,
        *,
        organization_id,
        user_id,
        include_disabled=False,
    ):
        return [
            profile
            for profile in self.profiles
            if profile.organization_id == organization_id
            and (
                profile.scope is RuntimeProfileScope.ORGANIZATION
                or (
                    profile.scope is RuntimeProfileScope.PERSONAL
                    and profile.user_id == user_id
                )
            )
        ]

    async def get_visible_by_id(self, *, profile_id, organization_id, user_id):
        for profile in await self.get_visible(
            organization_id=organization_id,
            user_id=user_id,
        ):
            if profile.id == profile_id:
                return profile
        return None

    async def create(self, profile):
        self.profiles.append(profile)
        return profile


class _DaemonRepository:
    def __init__(self, daemons):
        self.daemons = daemons

    async def get_for_user(self, *, daemon_id, user_id):
        for daemon in self.daemons:
            if daemon.id == daemon_id and daemon.user_id == user_id:
                return daemon
        return None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("codex", HarnessKind.CODEX),
        ("claude_code", HarnessKind.CLAUDE_CODE),
        ("opencode", HarnessKind.OPENCODE),
        ("cursor", HarnessKind.CURSOR),
        ("cursor_agent", HarnessKind.CURSOR),
        ("antigravity", HarnessKind.ANTIGRAVITY),
        ("agy", HarnessKind.ANTIGRAVITY),
        ("pydantic_ai", HarnessKind.LEMMA),
    ],
)
def test_harness_kind_accepts_legacy_aliases(raw, expected):
    assert HarnessKind(raw) is expected


@pytest.mark.asyncio
async def test_runtime_resolves_org_profile_model_override():
    org_id = uuid4()
    org_profile = _test_profile(
        scope=RuntimeProfileScope.ORGANIZATION,
        organization_id=org_id,
        name="org-default",
    )
    service = AgentRuntimeProfileService(_ProfileRepository([org_profile]))

    resolved_default = await service.resolve(
        runtime=AgentRuntimeConfig(profile_id=org_profile.id),
        organization_id=org_id,
        user_id=uuid4(),
    )
    resolved_override = await service.resolve(
        runtime=AgentRuntimeConfig(
            profile_id=org_profile.id,
            model_name="deepseek-v4-pro",
        ),
        organization_id=org_id,
        user_id=uuid4(),
    )

    assert resolved_default.model.name == "default"
    assert resolved_default.model_name_for_harness == "provider/org-default"
    assert resolved_override.model.name == "deepseek-v4-pro"
    assert resolved_override.model_name_for_harness == "provider/org-default/deepseek"


def test_runtime_credentials_are_redacted_in_repr_but_revealable():
    """API keys are SecretStr, so they never appear in repr()/logs/tracebacks
    (the leak that exposed a key in pytest's --showlocals dump), yet
    reveal_credentials still returns the plaintext for harness auth."""
    from app.modules.agent.domain.runtime_profiles import (
        ApiKeyRuntimeCredentials,
        reveal_credentials,
    )

    creds = ApiKeyRuntimeCredentials(api_key="super-secret-key")

    assert "super-secret-key" not in repr(creds)
    assert "super-secret-key" not in str(creds)

    profile = _test_profile(
        scope=RuntimeProfileScope.ORGANIZATION,
        organization_id=uuid4(),
        name="org-default",
    ).model_copy(update={"credentials": creds})
    # A profile that carries credentials must not leak them via repr either.
    assert "super-secret-key" not in repr(profile)
    # public_dict already drops credentials entirely.
    assert "credentials" not in profile.public_dict()

    # The one place the plaintext is recovered — for harness authentication.
    assert reveal_credentials(creds) == {"api_key": "super-secret-key"}
    assert reveal_credentials(None) is None


@pytest.mark.asyncio
async def test_resolve_unwraps_credentials_for_harness():
    from app.modules.agent.domain.runtime_profiles import ApiKeyRuntimeCredentials

    org_id = uuid4()
    profile = _test_profile(
        scope=RuntimeProfileScope.ORGANIZATION,
        organization_id=org_id,
        name="org-default",
    ).model_copy(update={"credentials": ApiKeyRuntimeCredentials(api_key="real-key")})
    service = AgentRuntimeProfileService(_ProfileRepository([profile]))

    resolved = await service.resolve(
        runtime=AgentRuntimeConfig(profile_id=profile.id),
        organization_id=org_id,
        user_id=uuid4(),
    )

    # The harness needs the real key, so resolve unwraps the SecretStr...
    assert resolved.credentials == {"api_key": "real-key"}
    # ...while the underlying profile object still masks it.
    assert "real-key" not in repr(resolved.profile)


def test_credentials_survive_persist_load_round_trip():
    """Mirrors the repository's persist (reveal_credentials -> encrypt) and load
    (decrypt -> model_validate) path. The real key must survive — serializing
    credentials with model_dump(mode='json') would have stored the masked
    '**********' and silently corrupted the key on save."""
    from app.modules.agent.domain.runtime_profiles import (
        AgentRuntimeProfile,
        ApiKeyRuntimeCredentials,
        reveal_credentials,
    )

    profile = _test_profile(
        scope=RuntimeProfileScope.ORGANIZATION,
        organization_id=uuid4(),
        name="org-default",
    ).model_copy(update={"credentials": ApiKeyRuntimeCredentials(api_key="persist-key")})

    # Persist side: what the repository hands to encrypt_json.
    stored = reveal_credentials(profile.credentials)
    assert stored == {"api_key": "persist-key"}

    # Load side: rebuild the entity from the decrypted plaintext dict.
    data = profile.model_dump(mode="json", exclude={"credentials"})
    data["credentials"] = stored
    reloaded = AgentRuntimeProfile.model_validate(data)

    assert reveal_credentials(reloaded.credentials) == {"api_key": "persist-key"}
    assert "persist-key" not in repr(reloaded)


@pytest.mark.asyncio
async def test_runtime_lists_configured_system_org_and_owned_personal_profiles(
    monkeypatch,
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "lemma_openai_api_key", "lemma-secret")
    # The system model profile has no built-in model default; configure one.
    monkeypatch.setattr(settings, "lemma_openai_model_names", "gpt-4o,gpt-4o-mini")
    monkeypatch.setattr(settings, "lemma_openai_default_model", "gpt-4o")
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_MODEL_NAMES", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_DEFAULT_MODEL", raising=False)
    org_id = uuid4()
    user_id = uuid4()
    other_org_id = uuid4()
    org_profile = _test_profile(
        scope=RuntimeProfileScope.ORGANIZATION,
        organization_id=org_id,
        name="org-default",
    )
    other_profile = _test_profile(
        scope=RuntimeProfileScope.ORGANIZATION,
        organization_id=other_org_id,
        name="other-default",
    )
    personal_profile = _test_profile(
        scope=RuntimeProfileScope.PERSONAL,
        organization_id=org_id,
        user_id=user_id,
        name="personal-default",
    )
    other_personal_profile = _test_profile(
        scope=RuntimeProfileScope.PERSONAL,
        organization_id=org_id,
        user_id=uuid4(),
        name="other-personal-default",
    )
    service = AgentRuntimeProfileService(
        _ProfileRepository(
            [org_profile, other_profile, personal_profile, other_personal_profile]
        )
    )

    profiles = await service.list_profiles(organization_id=org_id, user_id=user_id)
    profile_ids = {profile.id for profile in profiles}

    assert DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID in profile_ids
    assert org_profile.id in profile_ids
    assert personal_profile.id in profile_ids
    assert other_profile.id not in profile_ids
    assert other_personal_profile.id not in profile_ids


@pytest.mark.asyncio
async def test_runtime_falls_back_when_model_not_in_selected_profile():
    # A pinned model that is no longer in the profile catalog (deprecated model,
    # swapped key) must degrade to the profile's default model rather than
    # hard-failing every run that relies on the profile.
    org_id = uuid4()
    org_profile = _test_profile(
        scope=RuntimeProfileScope.ORGANIZATION,
        organization_id=org_id,
        name="org-default",
    )
    service = AgentRuntimeProfileService(_ProfileRepository([org_profile]))

    resolved = await service.resolve(
        runtime=AgentRuntimeConfig(
            profile_id=org_profile.id,
            model_name="missing-model",
        ),
        organization_id=org_id,
        user_id=uuid4(),
    )

    assert resolved.model is not None
    assert resolved.model.name == org_profile.default_model_name


def test_system_runtime_profiles_return_empty_without_server_credentials(monkeypatch):
    from app.core.config import settings
    from app.modules.agent.services import runtime_profile_service

    monkeypatch.setattr(settings, "lemma_openai_api_key", None)
    monkeypatch.setattr(settings, "lemma_anthropic_api_key", None)
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LEMMA_ANTHROPIC_API_KEY", raising=False)
    # Keep the test hermetic: production loads the local ``.env`` for runtime
    # credentials, which would otherwise repopulate the keys we just cleared (and
    # make this depend on the developer's .env). Neutralize that reload here.
    monkeypatch.setattr(runtime_profile_service, "_load_runtime_env", lambda: None)

    assert AgentRuntimeProfileService().system_profiles() == []


def test_system_runtime_profiles_only_include_configured_system_lemma(monkeypatch):
    from app.core.config import settings
    from app.modules.agent.services import runtime_profile_service

    # Keep hermetic: the profile builder reloads the local ``.env`` and prefers
    # ``os.getenv`` over ``settings``, which would otherwise leak the developer's
    # real model list/credentials into this test. Neutralize the reload and clear
    # the env so the monkeypatched ``settings`` win.
    monkeypatch.setattr(runtime_profile_service, "_load_runtime_env", lambda: None)
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_MODEL_NAMES", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_VISION_MODEL_NAMES", raising=False)
    monkeypatch.setattr(settings, "lemma_openai_api_key", "lemma-secret")
    monkeypatch.setattr(settings, "lemma_openai_default_model", "model-fast")
    monkeypatch.setattr(
        settings,
        "lemma_openai_model_names",
        "model-fast,model-pro,model-vision",
    )

    profiles = AgentRuntimeProfileService().system_profiles()

    assert [profile.id for profile in profiles] == [
        DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
    ]
    assert all(profile.scope is RuntimeProfileScope.SYSTEM for profile in profiles)
    lemma_profile = profiles[0]
    assert lemma_profile.name == "Lemma"
    assert lemma_profile.default_model_name == "model-fast"
    assert lemma_profile.credentials is not None
    # The system profile uses model names verbatim — public name == provider name.
    system_catalog = [
        (model.name, model.provider_model_name)
        for model in lemma_profile.model_catalog
    ]
    assert system_catalog == [
        ("model-fast", "model-fast"),
        ("model-pro", "model-pro"),
        ("model-vision", "model-vision"),
    ]
    public_profile = lemma_profile.public_dict()
    assert public_profile["config"] == {}
    assert [
        model["provider_model_name"] for model in public_profile["model_catalog"]
    ] == [
        "model-fast",
        "model-pro",
        "model-vision",
    ]


def test_system_openai_catalog_declares_vision_per_model(monkeypatch):
    """view_image is gated per model. The standard OpenAI /models endpoint does
    not report modalities, so the operator opts image-capable models in via
    LEMMA_OPENAI_VISION_MODEL_NAMES; everything else stays text-only and the
    image tools are withheld there."""
    from app.core.config import settings
    from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability
    from app.modules.agent.services import runtime_profile_service

    # Hermetic: neutralize the .env reload and clear env so the monkeypatched
    # ``settings`` drive the catalog (see the sibling test above).
    monkeypatch.setattr(runtime_profile_service, "_load_runtime_env", lambda: None)
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_MODEL_NAMES", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_VISION_MODEL_NAMES", raising=False)
    monkeypatch.setattr(settings, "lemma_openai_api_key", "lemma-secret")
    monkeypatch.setattr(
        settings,
        "lemma_openai_model_names",
        "model-vision-a,model-text-a,model-vision-b,model-text-b",
    )
    monkeypatch.setattr(settings, "lemma_openai_default_model", "model-vision-a")
    monkeypatch.setattr(
        settings,
        "lemma_openai_vision_model_names",
        "model-vision-a,model-vision-b",
    )

    profile = AgentRuntimeProfileService().system_profiles()[0]
    vision_by_model = {
        model.name: RuntimeModelCapability.VISION in model.capabilities
        for model in profile.model_catalog
    }
    assert vision_by_model == {
        "model-vision-a": True,
        "model-vision-b": True,
        "model-text-a": False,
        "model-text-b": False,
    }
    # Structured output is not tracked per-model (universal), so the catalog only
    # ever carries TEXT/TOOLS plus VISION where supported.
    for model in profile.model_catalog:
        assert RuntimeModelCapability.STRUCTURED_OUTPUT not in model.capabilities


def test_system_anthropic_catalog_declares_vision_for_all_models(monkeypatch):
    from app.core.config import settings
    from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability

    monkeypatch.setenv("LEMMA_DEFAULT_MODEL_TYPE", "anthropic_compat")
    monkeypatch.setenv("LEMMA_ANTHROPIC_API_KEY", "lemma-anthropic-secret")
    monkeypatch.setenv("LEMMA_ANTHROPIC_BASE_URL", "https://anthropic.test")
    monkeypatch.setenv(
        "LEMMA_ANTHROPIC_MODEL_NAMES",
        "claude-sonnet-test,claude-haiku-test",
    )
    monkeypatch.setattr(settings, "lemma_openai_api_key", None)

    profile = AgentRuntimeProfileService().system_profiles()[0]
    assert all(
        RuntimeModelCapability.VISION in model.capabilities
        for model in profile.model_catalog
    )


def test_system_runtime_profile_can_use_anthropic_compatible_env(monkeypatch):
    from app.core.config import settings

    monkeypatch.setenv("LEMMA_DEFAULT_MODEL_TYPE", "anthropic_compat")
    monkeypatch.setenv("LEMMA_ANTHROPIC_API_KEY", "lemma-anthropic-secret")
    monkeypatch.setenv("LEMMA_ANTHROPIC_BASE_URL", "https://anthropic.test")
    monkeypatch.setenv(
        "LEMMA_ANTHROPIC_MODEL_NAMES",
        "claude-sonnet-test,claude-haiku-test",
    )
    monkeypatch.setenv("LEMMA_ANTHROPIC_DEFAULT_MODEL", "claude-haiku-test")
    monkeypatch.setattr(settings, "lemma_openai_api_key", None)

    profiles = AgentRuntimeProfileService().system_profiles()

    assert [profile.id for profile in profiles] == [
        DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
    ]
    profile = profiles[0]
    assert profile.protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE
    assert profile.default_model_name == "claude-haiku-test"
    assert [model.name for model in profile.model_catalog] == [
        "claude-sonnet-test",
        "claude-haiku-test",
    ]
    assert str(profile.config.base_url) == "https://anthropic.test/"


@pytest.mark.parametrize(
    ("protocol", "harness_kind"),
    [
        (RuntimeProfileProtocol.CODEX_APP_SERVER, HarnessKind.CODEX),
        (RuntimeProfileProtocol.CLAUDE_CODE, HarnessKind.CLAUDE_CODE),
        (RuntimeProfileProtocol.OPENCODE, HarnessKind.OPENCODE),
        (RuntimeProfileProtocol.CURSOR, HarnessKind.CURSOR),
        (RuntimeProfileProtocol.ANTIGRAVITY, HarnessKind.ANTIGRAVITY),
    ],
)
@pytest.mark.asyncio
async def test_runtime_resolves_org_local_harness_profiles(protocol, harness_kind):
    org_id = uuid4()
    profile = _test_harness_profile(
        organization_id=org_id,
        name=harness_kind.value.lower(),
        protocol=protocol,
    )
    service = AgentRuntimeProfileService(_ProfileRepository([profile]))

    resolved = await service.resolve(
        runtime=AgentRuntimeConfig(profile_id=profile.id),
        organization_id=org_id,
        user_id=uuid4(),
    )

    assert resolved.harness_kind is harness_kind
    assert resolved.model_name_for_harness == "default"


@pytest.mark.asyncio
async def test_create_user_daemon_profile_from_catalog():
    org_id = uuid4()
    user_id = uuid4()
    daemon_id = uuid4()
    repo = _ProfileRepository([])
    daemon_repo = _DaemonRepository(
        [
            SimpleNamespace(
                id=daemon_id,
                user_id=user_id,
                harness_catalog={
                    "OPENCODE": {
                        "available": True,
                        "models": ["opencode/deepseek-v4-flash-free"],
                    }
                },
            )
        ]
    )
    service = AgentRuntimeProfileService(repo, daemon_repository=daemon_repo)

    profile = await service.create_user_daemon_profile(
        organization_id=org_id,
        user_id=user_id,
        daemon_id=daemon_id,
        harness_kind=HarnessKind.OPENCODE,
        name=" OpenCode daemon ",
        default_model_name="opencode/deepseek-v4-flash-free",
    )

    assert profile in repo.profiles
    assert profile.organization_id == org_id
    assert profile.user_id == user_id
    assert profile.daemon_id == daemon_id
    assert profile.scope is RuntimeProfileScope.ORGANIZATION
    assert profile.name == "OpenCode daemon"
    assert profile.protocol is RuntimeProfileProtocol.OPENCODE
    assert profile.derived_harness_kind() is HarnessKind.OPENCODE
    assert profile.default_model_name == "opencode/deepseek-v4-flash-free"
    assert [model.name for model in profile.model_catalog] == [
        "default",
        "opencode/deepseek-v4-flash-free",
    ]
    assert profile.public_dict()["config"] == {}
    assert profile.metadata == {"source": "USER_DAEMON"}


@pytest.mark.asyncio
async def test_create_user_daemon_profile_maps_claude_standard_context_models():
    org_id = uuid4()
    user_id = uuid4()
    daemon_id = uuid4()
    repo = _ProfileRepository([])
    daemon_repo = _DaemonRepository(
        [
            SimpleNamespace(
                id=daemon_id,
                user_id=user_id,
                harness_catalog={
                    "CLAUDE_CODE": {
                        "available": True,
                        "models": ["sonnet", "opus"],
                        "model_catalog": [
                            {
                                "name": "sonnet",
                                "display_name": "Claude Sonnet 4.6",
                                "provider_model_name": "claude-sonnet-4-6",
                                "metadata": {"context_window": "standard"},
                            },
                            {
                                "name": "opus",
                                "display_name": "Claude Opus 4.8",
                                "provider_model_name": "claude-opus-4-8",
                                "metadata": {"context_window": "standard"},
                            },
                        ],
                    }
                },
            )
        ]
    )
    service = AgentRuntimeProfileService(repo, daemon_repository=daemon_repo)

    profile = await service.create_user_daemon_profile(
        organization_id=org_id,
        user_id=user_id,
        daemon_id=daemon_id,
        harness_kind=HarnessKind.CLAUDE_CODE,
        name="Claude Code daemon",
        default_model_name="sonnet",
    )

    by_name = {entry.name: entry for entry in profile.model_catalog}
    # Default leads the catalog; the friendly alias stays the selection name but
    # carries the full standard-context id + advertising metadata.
    assert profile.model_catalog[0].name == "default"
    assert by_name["sonnet"].provider_model_name == "claude-sonnet-4-6"
    assert by_name["sonnet"].display_name == "Claude Sonnet 4.6"
    assert by_name["sonnet"].metadata["context_window"] == "standard"
    assert by_name["opus"].provider_model_name == "claude-opus-4-8"

    # Resolving the alias hands the harness the standard-context id, so a user
    # without usage credits never hits the 1M-context failure.
    resolved = await service.resolve(
        runtime=AgentRuntimeConfig(profile_id=profile.id, model_name="sonnet"),
        organization_id=org_id,
        user_id=user_id,
    )
    assert resolved.model_name_for_harness == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_create_user_daemon_profile_rejects_unavailable_harness():
    user_id = uuid4()
    daemon_id = uuid4()
    service = AgentRuntimeProfileService(
        _ProfileRepository([]),
        daemon_repository=_DaemonRepository(
            [
                SimpleNamespace(
                    id=daemon_id,
                    user_id=user_id,
                    harness_catalog={"CODEX": {"available": False}},
                )
            ]
        ),
    )

    with pytest.raises(ValueError, match="not available"):
        await service.create_user_daemon_profile(
            organization_id=uuid4(),
            user_id=user_id,
            daemon_id=daemon_id,
            harness_kind=HarnessKind.CODEX,
            name="Codex daemon",
        )


@pytest.mark.asyncio
async def test_create_user_daemon_profile_rejects_unknown_model():
    user_id = uuid4()
    daemon_id = uuid4()
    service = AgentRuntimeProfileService(
        _ProfileRepository([]),
        daemon_repository=_DaemonRepository(
            [
                SimpleNamespace(
                    id=daemon_id,
                    user_id=user_id,
                    harness_catalog={
                        "OPENCODE": {
                            "available": True,
                            "models": ["opencode/deepseek-v4-flash-free"],
                        }
                    },
                )
            ]
        ),
    )

    with pytest.raises(ValueError, match="detected model names"):
        await service.create_user_daemon_profile(
            organization_id=uuid4(),
            user_id=user_id,
            daemon_id=daemon_id,
            harness_kind=HarnessKind.OPENCODE,
            name="OpenCode daemon",
            default_model_name="opencode/missing",
        )


@pytest.mark.asyncio
async def test_create_openai_compatible_profile_discovers_provider_models(monkeypatch):
    from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability

    async def fake_discover(**_kwargs):
        # OpenRouter-style discovery reports image input per model, so vision is
        # auto-detected without any explicit configuration.
        return [
            DiscoveredModel("openrouter/deepseek/deepseek-chat"),
            DiscoveredModel("openai/gpt-5.1", supports_vision=True),
        ]

    monkeypatch.setattr(
        "app.modules.agent.services.runtime_profile_service._discover_openai_compatible_models",
        fake_discover,
    )
    org_id = uuid4()
    repo = _ProfileRepository([])
    service = AgentRuntimeProfileService(repo)

    profile = await service.create_openai_compatible_profile(
        organization_id=org_id,
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="openrouter-secret",
        default_model_name="openai/gpt-5.1",
        headers={
            "HTTP-Referer": "https://lemma.test",
            "Authorization": "Bearer old-secret",
        },
        model_settings={
            "temperature": 0.2,
            "nested": {
                "api_key": "nested-secret",
                "items": [{"refresh_token": "refresh-secret"}],
            },
        },
    )

    assert profile in repo.profiles
    assert profile.protocol is RuntimeProfileProtocol.OPENAI_COMPATIBLE
    assert profile.default_model_name == "openai/gpt-5.1"
    assert [model.name for model in profile.model_catalog] == [
        "openrouter/deepseek/deepseek-chat",
        "openai/gpt-5.1",
    ]
    vision_by_model = {
        model.name: RuntimeModelCapability.VISION in model.capabilities
        for model in profile.model_catalog
    }
    assert vision_by_model == {
        "openrouter/deepseek/deepseek-chat": False,
        "openai/gpt-5.1": True,
    }
    assert profile.has_credentials is True
    public = profile.public_dict()
    assert public["has_credentials"] is True
    assert public["config"]["base_url"] == "https://openrouter.ai/api/v1"
    assert public["config"]["headers"] == {
        "HTTP-Referer": "https://lemma.test",
        "Authorization": "<redacted>",
    }
    assert public["config"]["model_settings"] == {
        "temperature": 0.2,
        "nested": {
            "api_key": "<redacted>",
            "items": [{"refresh_token": "<redacted>"}],
        },
    }


@pytest.mark.asyncio
async def test_create_openai_compatible_profile_uses_supplied_models_when_discovery_fails(
    monkeypatch,
):
    async def fake_discover(**_kwargs):
        return []

    monkeypatch.setattr(
        "app.modules.agent.services.runtime_profile_service._discover_openai_compatible_models",
        fake_discover,
    )
    service = AgentRuntimeProfileService(_ProfileRepository([]))

    profile = await service.create_openai_compatible_profile(
        organization_id=uuid4(),
        name="Custom provider",
        base_url="https://api.vendor.test/v1",
        api_key="vendor-secret",
        default_model_name="vendor/model-pro",
        model_names=["vendor/model-pro"],
    )

    assert profile.default_model_name == "vendor/model-pro"
    assert profile.model_catalog[0].provider_model_name == "vendor/model-pro"
    assert profile.metadata["catalog_discovered"] is False


@pytest.mark.asyncio
async def test_create_provider_profile_requires_discovery_or_model_names(monkeypatch):
    async def fake_discover(**_kwargs):
        return []

    monkeypatch.setattr(
        "app.modules.agent.services.runtime_profile_service._discover_openai_compatible_models",
        fake_discover,
    )
    service = AgentRuntimeProfileService(_ProfileRepository([]))

    with pytest.raises(ValueError, match="provide model_names"):
        await service.create_openai_compatible_profile(
            organization_id=uuid4(),
            name="Unknown provider",
            base_url="https://provider.test/v1",
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://169.254.169.254/v1",  # cloud metadata (link-local) — rejected in all modes
        "http://10.0.0.5/v1",  # private RFC1918 — rejected in all modes
        "ftp://example.com/v1",  # non-http(s) scheme
    ],
)
@pytest.mark.asyncio
async def test_create_openai_compatible_profile_rejects_ssrf_base_url(base_url):
    """A caller-supplied base_url that targets a private/link-local/non-http(s)
    address must be rejected before any server-side request is issued."""
    service = AgentRuntimeProfileService(_ProfileRepository([]))
    with pytest.raises(ValueError, match="public http"):
        await service.create_openai_compatible_profile(
            organization_id=uuid4(),
            name="evil",
            base_url=base_url,
            api_key="x",
            model_names=["m"],
        )


@pytest.mark.asyncio
async def test_create_anthropic_compatible_profile_discovers_provider_models(
    monkeypatch,
):
    from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability

    async def fake_discover(**_kwargs):
        return [DiscoveredModel("claude-sonnet-4-5-20250929")]

    monkeypatch.setattr(
        "app.modules.agent.services.runtime_profile_service._discover_anthropic_compatible_models",
        fake_discover,
    )
    service = AgentRuntimeProfileService(_ProfileRepository([]))

    profile = await service.create_anthropic_compatible_profile(
        organization_id=uuid4(),
        name="Anthropic",
        api_key="anthropic-secret",
    )

    assert profile.protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE
    assert profile.default_model_name == "claude-sonnet-4-5-20250929"
    assert profile.has_credentials is True
    # Anthropic/Claude models are uniformly multimodal, so every catalog entry
    # keeps VISION regardless of what discovery reports.
    assert all(
        RuntimeModelCapability.VISION in model.capabilities
        for model in profile.model_catalog
    )


def test_lemma_harness_builds_dynamic_openai_compatible_model():
    model = _runtime_profile_model(
        HarnessOptions(
            model_name="vendor/model-pro",
            extra={
                "runtime_profile": {
                    "protocol": "OPENAI_COMPATIBLE",
                    "config": {
                        "base_url": "https://api.vendor.test/v1",
                        "headers": {"X-Test": "yes"},
                    },
                },
                "runtime_credentials": {"api_key": "secret"},
            },
        )
    )

    assert model is not None
    assert type(model).__name__ == "OpenAIChatModel"


def test_lemma_harness_builds_dynamic_anthropic_compatible_model():
    model = _runtime_profile_model(
        HarnessOptions(
            model_name="claude-sonnet-4-5-20250929",
            extra={
                "runtime_profile": {
                    "protocol": "ANTHROPIC_COMPATIBLE",
                    "config": {"base_url": "https://api.anthropic.com"},
                },
                "runtime_credentials": {"api_key": "secret"},
            },
        )
    )

    assert model is not None
    assert type(model).__name__ == "AnthropicModel"


def test_default_runtime_uses_system_profile(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(
        settings,
        "local_agent_runtime_config_path",
        str(tmp_path / "missing-runtime.json"),
    )

    monkeypatch.setattr(settings, "environment", "local")
    assert default_agent_runtime_profile_id() == DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID

    monkeypatch.setattr(settings, "environment", "development")
    assert default_agent_runtime_profile_id() == DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID


def test_local_default_runtime_can_be_file_backed(tmp_path):
    service = AgentRuntimeDefaultService(
        environment="local",
        config_path=tmp_path / "agent-runtime.json",
    )

    updated = service.set_default(AgentRuntimeConfig(profile_id="user-profile"))

    assert updated.profile_id == "user-profile"
    assert service.get_default() == updated


def test_agent_runtime_config_rejects_empty_profile_id():
    with pytest.raises(ValueError):
        AgentRuntimeConfig(profile_id=" ")


def test_agent_runtime_config_rejects_empty_model_name():
    with pytest.raises(ValueError):
        AgentRuntimeConfig(profile_id="system:lemma", model_name=" ")


def test_default_runtime_cannot_be_changed_outside_local(tmp_path):
    service = AgentRuntimeDefaultService(
        environment="development",
        config_path=tmp_path / "agent-runtime.json",
    )

    with pytest.raises(AgentRuntimeDefaultError):
        service.set_default(AgentRuntimeConfig(profile_id="system:lemma"))


def test_selected_model_returns_requested_when_in_catalog():
    profile = _test_profile(scope=RuntimeProfileScope.SYSTEM, name="p")
    model = _selected_model(profile, "deepseek-v4-pro")
    assert model is not None and model.name == "deepseek-v4-pro"


def test_selected_model_falls_back_to_profile_default_when_no_request():
    profile = _test_profile(scope=RuntimeProfileScope.SYSTEM, name="p")
    model = _selected_model(profile, None)
    assert model is not None and model.name == "default"


def test_selected_model_pinned_missing_falls_back_to_default_not_raise():
    # A pinned model that is no longer in the catalog (e.g. deprecated) must
    # degrade to the profile default rather than hard-failing the run.
    profile = _test_profile(scope=RuntimeProfileScope.SYSTEM, name="p")
    model = _selected_model(profile, "model-that-was-removed")
    assert model is not None and model.name == "default"


def test_selected_model_pinned_missing_and_default_missing_uses_first_entry():
    profile = _test_profile(scope=RuntimeProfileScope.SYSTEM, name="p")
    profile.default_model_name = "also-gone"
    model = _selected_model(profile, "model-that-was-removed")
    assert model is not None and model.name == "default"  # first catalog entry


def test_selected_model_empty_catalog_returns_none():
    profile = _test_profile(scope=RuntimeProfileScope.SYSTEM, name="p")
    profile.model_catalog = []
    profile.default_model_name = None
    assert _selected_model(profile, None) is None
