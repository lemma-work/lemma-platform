from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    HarnessRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCatalogEntry,
    RuntimeProfileScope,
    RuntimeProfileStatus,
    RuntimeProfileType,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services import runtime_profile_service as service_module
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
    DiscoveredModel,
)


class ProfileRepository:
    def __init__(self, profiles: list[AgentRuntimeProfile] | None = None):
        self.profiles = profiles or []

    async def create(self, profile: AgentRuntimeProfile) -> AgentRuntimeProfile:
        self.profiles.append(profile)
        return profile

    async def update(self, profile: AgentRuntimeProfile) -> AgentRuntimeProfile:
        self.profiles = [
            profile if existing.id == profile.id else existing
            for existing in self.profiles
        ]
        return profile

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
                or profile.owner_user_id == user_id
            )
            and (
                include_disabled
                or profile.status is not RuntimeProfileStatus.DISABLED
            )
        ]

    async def get_visible_by_id(
        self,
        *,
        profile_id,
        organization_id,
        user_id,
        include_disabled=False,
    ):
        return next(
            (
                profile
                for profile in await self.get_visible(
                    organization_id=organization_id,
                    user_id=user_id,
                    include_disabled=include_disabled,
                )
                if profile.id == profile_id
            ),
            None,
        )


class HostRepository:
    def __init__(self, *, host, harness):
        self.host = host
        self.harness = harness

    async def get_harness(self, *, harness_id):
        return self.harness if harness_id == self.harness.id else None

    async def get_for_user(self, *, host_id, user_id):
        if self.host.id == host_id and self.host.user_id == user_id:
            return self.host
        return None


def provider_profile(
    *,
    organization_id,
    owner_user_id=None,
    scope=RuntimeProfileScope.ORGANIZATION,
) -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        scope=scope,
        runtime_type=RuntimeProfileType.OPENAI_COMPATIBLE,
        name="Provider",
        default_model_name="model-a",
        model_catalog=[
            RuntimeModelCatalogEntry(
                name="model-a",
                provider_model_name="upstream/model-a",
            ),
            RuntimeModelCatalogEntry(
                name="model-b",
                provider_model_name="upstream/model-b",
            ),
        ],
        config=OpenAICompatibleRuntimeConfig(base_url="https://provider.test/v1"),
    )


def harness_fixture(*, organization_id, owner_user_id, paired_organization=True):
    now = datetime.now(timezone.utc)
    host_id = uuid4()
    harness_id = uuid4()
    host = SimpleNamespace(
        id=host_id,
        user_id=owner_user_id,
        organization_id=organization_id if paired_organization else None,
        revoked_at=None,
        status="ONLINE",
        last_seen_at=now,
    )
    harness = SimpleNamespace(
        id=harness_id,
        host_id=host_id,
        health="READY",
        stale_after=now + timedelta(hours=1),
        config_revision="revision-1",
        config_options=[
            {
                "id": "model",
                "name": "Model",
                "category": "model",
                "required": False,
                "options": [{"value": "model-a"}, {"value": "model-b"}],
            },
            {
                "id": "mode",
                "name": "Mode",
                "category": "mode",
                "required": True,
                "options": [{"value": "safe"}, {"value": "full"}],
            },
        ],
    )
    return host, harness


def test_profile_invariants_are_minimal_and_explicit():
    organization_id = uuid4()
    user_id = uuid4()
    profile = provider_profile(
        organization_id=organization_id,
        owner_user_id=user_id,
        scope=RuntimeProfileScope.PERSONAL,
    )
    assert profile.owner_user_id == user_id
    assert profile.harness_id is None

    with pytest.raises(ValidationError, match="PERSONAL profile requires owner_user_id"):
        provider_profile(
            organization_id=organization_id,
            scope=RuntimeProfileScope.PERSONAL,
        )
    with pytest.raises(
        ValidationError,
        match="ORGANIZATION profile cannot have owner_user_id",
    ):
        provider_profile(
            organization_id=organization_id,
            owner_user_id=user_id,
        )


def test_provider_requires_default_and_rejects_harness_binding():
    organization_id = uuid4()
    payload = provider_profile(organization_id=organization_id).model_dump()
    payload["default_model_name"] = None
    with pytest.raises(ValidationError, match="requires default_model_name"):
        AgentRuntimeProfile.model_validate(payload)
    payload["default_model_name"] = "model-a"
    payload["harness_id"] = uuid4()
    with pytest.raises(ValidationError, match="cannot reference a harness"):
        AgentRuntimeProfile.model_validate(payload)


def test_harness_requires_binding_and_may_follow_harness_default():
    profile = AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=uuid4(),
        owner_user_id=uuid4(),
        harness_id=uuid4(),
        scope=RuntimeProfileScope.PERSONAL,
        runtime_type=RuntimeProfileType.HARNESS,
        name="Codex",
        default_model_name=None,
        config=HarnessRuntimeConfig(
            harness_snapshot_revision="revision-1",
            config_selections={},
        ),
    )
    assert profile.default_model_name is None
    assert "FOLLOW_ADAPTER_DEFAULT" not in profile.model_dump_json()


def test_profile_credentials_are_typed_by_runtime():
    organization_id = uuid4()
    provider = provider_profile(organization_id=organization_id)
    payload = provider.model_dump()
    payload["credentials"] = {
        "service_account_json": {"project_id": "wrong-provider"}
    }
    with pytest.raises(ValidationError, match="incompatible credentials"):
        AgentRuntimeProfile.model_validate(payload)

    harness_payload = {
        "id": str(uuid4()),
        "organization_id": organization_id,
        "owner_user_id": uuid4(),
        "harness_id": uuid4(),
        "scope": RuntimeProfileScope.PERSONAL,
        "runtime_type": RuntimeProfileType.HARNESS,
        "name": "Codex",
        "config": {"harness_snapshot_revision": "revision-1"},
        "credentials": {"api_key": "must-live-on-host"},
    }
    with pytest.raises(ValidationError, match="cannot contain credentials"):
        AgentRuntimeProfile.model_validate(harness_payload)


@pytest.mark.asyncio
async def test_resolution_is_strict_and_never_substitutes_models():
    organization_id = uuid4()
    profile = provider_profile(organization_id=organization_id)
    service = AgentRuntimeProfileService(ProfileRepository([profile]))
    resolved = await service.resolve(
        runtime=AgentRuntimeConfig(
            profile_id=profile.id,
            model_name="model-b",
        ),
        organization_id=organization_id,
        user_id=uuid4(),
    )
    assert resolved.provider_model_name == "upstream/model-b"
    with pytest.raises(RuntimeError, match="no selectable model"):
        await service.resolve(
            runtime=AgentRuntimeConfig(
                profile_id=profile.id,
                model_name="missing",
            ),
            organization_id=organization_id,
            user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_provider_creation_defaults_personal_and_validates_explicit_default(
    monkeypatch,
):
    async def discover(**_kwargs):
        return [DiscoveredModel("model-a"), DiscoveredModel("model-b")]

    monkeypatch.setattr(
        service_module,
        "_discover_openai_compatible_models",
        discover,
    )
    organization_id = uuid4()
    user_id = uuid4()
    repository = ProfileRepository()
    service = AgentRuntimeProfileService(repository)
    profile = await service.create_openai_compatible_profile(
        organization_id=organization_id,
        user_id=user_id,
        name="Provider",
        base_url="https://provider.test/v1",
        default_model_name="model-b",
    )
    assert profile.scope is RuntimeProfileScope.PERSONAL
    assert profile.owner_user_id == user_id
    assert profile.default_model_name == "model-b"
    with pytest.raises(ValueError, match="must be one of"):
        await service.create_openai_compatible_profile(
            organization_id=organization_id,
            user_id=user_id,
            name="Broken",
            base_url="https://provider.test/v1",
            default_model_name="missing",
        )


@pytest.mark.asyncio
async def test_declared_cloud_provider_types_create_minimal_typed_profiles():
    organization_id = uuid4()
    user_id = uuid4()
    service = AgentRuntimeProfileService(ProfileRepository())

    azure = await service.create_azure_openai_profile(
        organization_id=organization_id,
        user_id=user_id,
        name="Azure",
        scope=RuntimeProfileScope.PERSONAL,
        azure_endpoint="https://example.openai.azure.com/openai/v1",
        api_version=None,
        api_key="azure-secret",
        default_model_name="deployment-a",
        model_names=["deployment-a"],
    )
    assert azure.runtime_type is RuntimeProfileType.AZURE_OPENAI
    assert azure.default_model_name == "deployment-a"
    assert azure.config.__class__.__name__ == "AzureOpenAIRuntimeConfig"
    assert "deployment_id" not in azure.config.model_dump()

    vertex = await service.create_google_vertex_profile(
        organization_id=organization_id,
        user_id=user_id,
        name="Vertex",
        scope=RuntimeProfileScope.ORGANIZATION,
        project_id="lemma-project",
        location="us-central1",
        default_model_name="gemini-2.5-pro",
        model_names=["gemini-2.5-pro"],
    )
    assert vertex.runtime_type is RuntimeProfileType.GOOGLE_VERTEX
    assert vertex.config.__class__.__name__ == "GoogleVertexRuntimeConfig"
    assert vertex.owner_user_id is None
    assert vertex.default_model_name == "gemini-2.5-pro"


@pytest.mark.asyncio
async def test_organization_harness_profile_is_explicit_delegation():
    organization_id = uuid4()
    owner_user_id = uuid4()
    host, harness = harness_fixture(
        organization_id=organization_id,
        owner_user_id=owner_user_id,
    )
    repository = ProfileRepository()
    service = AgentRuntimeProfileService(
        repository,
        HostRepository(host=host, harness=harness),
    )
    profile = await service.create_harness_profile(
        organization_id=organization_id,
        user_id=owner_user_id,
        harness_id=harness.id,
        scope=RuntimeProfileScope.ORGANIZATION,
        name="Shared Codex",
        harness_snapshot_revision="revision-1",
        config_selections={"mode": "safe"},
    )
    assert profile.owner_user_id is None
    assert profile.harness_id == harness.id
    assert profile.default_model_name is None


@pytest.mark.asyncio
async def test_organization_harness_requires_organization_pairing():
    organization_id = uuid4()
    owner_user_id = uuid4()
    host, harness = harness_fixture(
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        paired_organization=False,
    )
    service = AgentRuntimeProfileService(
        ProfileRepository(),
        HostRepository(host=host, harness=harness),
    )
    with pytest.raises(ValueError, match="require an organization pairing"):
        await service.create_harness_profile(
            organization_id=organization_id,
            user_id=owner_user_id,
            harness_id=harness.id,
            scope=RuntimeProfileScope.ORGANIZATION,
            name="Shared Codex",
            harness_snapshot_revision="revision-1",
            config_selections={"mode": "safe"},
        )


@pytest.mark.asyncio
async def test_harness_revision_selections_and_model_are_strict():
    organization_id = uuid4()
    owner_user_id = uuid4()
    host, harness = harness_fixture(
        organization_id=organization_id,
        owner_user_id=owner_user_id,
    )
    service = AgentRuntimeProfileService(
        ProfileRepository(),
        HostRepository(host=host, harness=harness),
    )
    common = dict(
        organization_id=organization_id,
        user_id=owner_user_id,
        harness_id=harness.id,
        scope=RuntimeProfileScope.PERSONAL,
        name="Codex",
    )
    with pytest.raises(ValueError, match="changed"):
        await service.create_harness_profile(
            **common,
            harness_snapshot_revision="stale",
            config_selections={"mode": "safe"},
        )
    with pytest.raises(ValueError, match="Unknown"):
        await service.create_harness_profile(
            **common,
            harness_snapshot_revision="revision-1",
            config_selections={"unknown": "value"},
        )
    with pytest.raises(ValueError, match="not offered"):
        await service.create_harness_profile(
            **common,
            harness_snapshot_revision="revision-1",
            config_selections={"mode": "safe"},
            default_model_name="missing",
        )


@pytest.mark.asyncio
async def test_delete_behavior_disables_referenced_profile():
    organization_id = uuid4()
    profile = provider_profile(organization_id=organization_id)
    repository = ProfileRepository([profile])
    service = AgentRuntimeProfileService(repository)
    disabled = await service.disable_profile(
        profile_id=profile.id,
        organization_id=organization_id,
        user_id=uuid4(),
    )
    assert disabled.status is RuntimeProfileStatus.DISABLED
