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


@pytest.mark.asyncio
async def test_runtime_lists_configured_system_org_and_owned_personal_profiles(
    monkeypatch,
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "lemma_openai_api_key", "lemma-secret")
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_API_KEY", raising=False)
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
async def test_runtime_rejects_model_not_in_selected_profile():
    org_id = uuid4()
    org_profile = _test_profile(
        scope=RuntimeProfileScope.ORGANIZATION,
        organization_id=org_id,
        name="org-default",
    )
    service = AgentRuntimeProfileService(_ProfileRepository([org_profile]))

    with pytest.raises(RuntimeError, match="not in runtime profile"):
        await service.resolve(
            runtime=AgentRuntimeConfig(
                profile_id=org_profile.id,
                model_name="missing-model",
            ),
            organization_id=org_id,
            user_id=uuid4(),
        )


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

    monkeypatch.setattr(settings, "lemma_openai_api_key", "lemma-secret")
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_API_KEY", raising=False)
    # Pin the Fireworks catalog explicitly: the shipped OSS defaults are now
    # provider-agnostic (OpenAI), so this exercises the model registry directly.
    monkeypatch.setattr(settings, "lemma_openai_default_model", "minimax-m3")
    monkeypatch.setattr(
        settings,
        "lemma_openai_model_names",
        "minimax-m3,glm-5.2,kimi-k2.7-code,kimi-k2.6,deepseek-v4-pro,deepseek-v4-flash",
    )
    monkeypatch.delenv("LEMMA_OPENAI_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_MODEL_NAMES", raising=False)

    profiles = AgentRuntimeProfileService().system_profiles()

    assert [profile.id for profile in profiles] == [
        DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
    ]
    assert all(profile.scope is RuntimeProfileScope.SYSTEM for profile in profiles)
    lemma_profile = profiles[0]
    assert lemma_profile.name == "Lemma"
    assert lemma_profile.default_model_name == "minimax-m3"
    assert lemma_profile.credentials is not None
    system_catalog = [
        (model.name, model.provider_model_name)
        for model in lemma_profile.model_catalog
    ]
    assert system_catalog == [
        ("minimax-m3", "accounts/fireworks/models/minimax-m3"),
        ("glm-5.2", "accounts/fireworks/models/glm-5p2"),
        ("kimi-k2.7-code", "accounts/fireworks/models/kimi-k2p7-code"),
        ("kimi-k2.6", "accounts/fireworks/models/kimi-k2p6"),
        ("deepseek-v4-pro", "accounts/fireworks/models/deepseek-v4-pro"),
        ("deepseek-v4-flash", "accounts/fireworks/models/deepseek-v4-flash"),
    ]
    public_profile = lemma_profile.public_dict()
    assert public_profile["config"] == {}
    assert [
        model["provider_model_name"] for model in public_profile["model_catalog"]
    ] == [
        "minimax-m3",
        "glm-5.2",
        "kimi-k2.7-code",
        "kimi-k2.6",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]


def test_system_openai_catalog_declares_vision_per_model(monkeypatch):
    """Image support is maintained against each model in the profile: m3 + kimi
    accept images (VISION), glm + deepseek do not, so view_image is withheld there."""
    from app.core.config import settings
    from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability

    monkeypatch.setattr(settings, "lemma_openai_api_key", "lemma-secret")
    monkeypatch.delenv("LEMMA_DEFAULT_MODEL_TYPE", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_API_KEY", raising=False)
    # Pin the Fireworks catalog explicitly (shipped defaults are OpenAI now).
    monkeypatch.setattr(
        settings,
        "lemma_openai_model_names",
        "minimax-m3,glm-5.2,kimi-k2.7-code,kimi-k2.6,deepseek-v4-pro,deepseek-v4-flash",
    )
    monkeypatch.setattr(settings, "lemma_openai_default_model", "minimax-m3")
    monkeypatch.delenv("LEMMA_OPENAI_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_MODEL_NAMES", raising=False)

    profile = AgentRuntimeProfileService().system_profiles()[0]
    vision_by_model = {
        model.name: RuntimeModelCapability.VISION in model.capabilities
        for model in profile.model_catalog
    }
    assert vision_by_model == {
        "minimax-m3": True,
        "kimi-k2.7-code": True,
        "kimi-k2.6": True,
        "glm-5.2": False,
        "deepseek-v4-pro": False,
        "deepseek-v4-flash": False,
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
    async def fake_discover(**_kwargs):
        return ["openrouter/deepseek/deepseek-chat", "openai/gpt-5.1"]

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
        name="Fireworks custom",
        base_url="https://api.fireworks.ai/inference/v1",
        api_key="fireworks-secret",
        default_model_name="accounts/fireworks/models/kimi-k2p6",
        model_names=["accounts/fireworks/models/kimi-k2p6"],
    )

    assert profile.default_model_name == "accounts/fireworks/models/kimi-k2p6"
    assert profile.model_catalog[0].provider_model_name == (
        "accounts/fireworks/models/kimi-k2p6"
    )
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
    async def fake_discover(**_kwargs):
        return ["claude-sonnet-4-5-20250929"]

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


def test_lemma_harness_builds_dynamic_openai_compatible_model():
    model = _runtime_profile_model(
        HarnessOptions(
            model_name="accounts/fireworks/models/kimi-k2p6",
            extra={
                "runtime_profile": {
                    "protocol": "OPENAI_COMPATIBLE",
                    "config": {
                        "base_url": "https://api.fireworks.ai/inference/v1",
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
