"""Background model calls on a deployment with no system model of its own.

A Desktop install -- or any self-host -- can be set up entirely through
Organization -> Models. Chat worked there; titles, schedule filters, README
polish and the vision delegate did not, because they only knew `system:lemma`.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.config import agent_settings
from app.core.config import settings
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services.runtime_profile_service import ResolvedAgentRuntime
from app.modules.agent.services.workspace_model_fallback import (
    choose_workspace_runtime,
    resolve_system_or_workspace_runtime,
)

pytestmark = pytest.mark.unit

_ORG = uuid4()
_USER = uuid4()
_TEXT = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
_SEES = [*_TEXT, RuntimeModelCapability.VISION]


def _entry(
    name: str, capabilities: list[RuntimeModelCapability]
) -> RuntimeModelCatalogEntry:
    return RuntimeModelCatalogEntry(
        name=name, provider_model_name=name, capabilities=capabilities
    )


def _provider(
    name: str,
    *entries: RuntimeModelCatalogEntry,
    scope: RuntimeProfileScope = RuntimeProfileScope.ORGANIZATION,
) -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=_ORG,
        user_id=_USER if scope is RuntimeProfileScope.PERSONAL else None,
        scope=scope,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name=name,
        default_model_name=entries[0].name,
        model_catalog=list(entries),
    )


def _harness() -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=_ORG,
        user_id=_USER,
        harness_id=uuid4(),
        scope=RuntimeProfileScope.PERSONAL,
        kind=RuntimeProfileKind.HARNESS,
        protocol=RuntimeProfileProtocol.AGENT_HOST,
        name="A coding agent",
        model_catalog=[_entry("agent-model", _SEES)],
    )


class TestChoosingAWorkspaceModel:
    def test_the_pods_own_default_wins(self) -> None:
        org_wide = _provider("Alpha", _entry("alpha-1", _TEXT))
        pod_pick = _provider("Beta", _entry("beta-1", _TEXT), _entry("beta-2", _TEXT))

        runtime = choose_workspace_runtime(
            [org_wide, pod_pick],
            pod_default=AgentRuntimeConfig(profile_id=pod_pick.id, model_name="beta-2"),
            model_name=None,
            require_vision=False,
        )

        assert runtime == AgentRuntimeConfig(
            profile_id=pod_pick.id, model_name="beta-2"
        )

    def test_organization_providers_come_before_personal_ones(self) -> None:
        personal = _provider(
            "Mine", _entry("mine-1", _TEXT), scope=RuntimeProfileScope.PERSONAL
        )
        org_wide = _provider("Theirs", _entry("theirs-1", _TEXT))

        runtime = choose_workspace_runtime(
            [personal, org_wide],
            pod_default=None,
            model_name=None,
            require_vision=False,
        )

        assert runtime is not None
        assert runtime.profile_id == org_wide.id
        assert runtime.model_name == "theirs-1"

    def test_a_coding_agent_is_never_picked(self) -> None:
        """A one-shot title prompt cannot be sent to a harness."""
        assert (
            choose_workspace_runtime(
                [_harness()], pod_default=None, model_name=None, require_vision=False
            )
            is None
        )

    def test_a_configured_name_is_used_when_the_provider_serves_it(self) -> None:
        provider = _provider("Alpha", _entry("big", _TEXT), _entry("small", _TEXT))

        runtime = choose_workspace_runtime(
            [provider], pod_default=None, model_name="small", require_vision=False
        )

        assert runtime is not None and runtime.model_name == "small"

    def test_an_unknown_configured_name_falls_back_to_the_provider_default(
        self,
    ) -> None:
        provider = _provider("Alpha", _entry("big", _TEXT), _entry("small", _TEXT))

        runtime = choose_workspace_runtime(
            [provider], pod_default=None, model_name="elsewhere", require_vision=False
        )

        assert runtime is not None and runtime.model_name == "big"

    def test_vision_only_settles_for_a_model_that_reads_images(self) -> None:
        text_only = _provider("Alpha", _entry("words", _TEXT))
        mixed = _provider("Beta", _entry("words-too", _TEXT), _entry("eyes", _SEES))

        runtime = choose_workspace_runtime(
            [text_only, mixed], pod_default=None, model_name=None, require_vision=True
        )

        assert runtime == AgentRuntimeConfig(profile_id=mixed.id, model_name="eyes")

    def test_no_image_reading_model_means_none_not_a_text_model(self) -> None:
        assert (
            choose_workspace_runtime(
                [_provider("Alpha", _entry("words", _TEXT))],
                pod_default=None,
                model_name=None,
                require_vision=True,
            )
            is None
        )


@pytest.fixture
def no_system_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment whose environment supplies no model provider."""
    for name in (
        "LEMMA_OPENAI_API_KEY",
        "LEMMA_ANTHROPIC_API_KEY",
        "LEMMA_DEFAULT_MODEL_TYPE",
    ):
        monkeypatch.delenv(name, raising=False)
    # A desktop install reads no `.env`, so a developer's own keys in a
    # checkout's `.env` cannot turn this back into a configured deployment.
    monkeypatch.setenv("DEPLOYMENT_KIND", "desktop")
    monkeypatch.setattr(
        "app.modules.identity.config.identity_settings.deployment_kind", "desktop"
    )
    monkeypatch.setattr(settings, "lemma_openai_api_key", None)
    monkeypatch.setattr(agent_settings, "lemma_anthropic_api_key", None)
    monkeypatch.setattr(agent_settings, "lemma_default_model_type", "openai_compat")


class TestSystemOrWorkspace:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("no_system_model")
    async def test_no_system_model_falls_back_to_the_workspace(self) -> None:
        workspace = _provider("Alpha", _entry("alpha-1", _TEXT))
        resolved = ResolvedAgentRuntime(
            profile=workspace,
            harness_kind=workspace.derived_harness_kind(),
            model=workspace.model_catalog[0],
            provider_model_name="alpha-1",
            credentials=None,
        )
        asked: dict[str, object] = {}

        async def workspace_runtime(
            *,
            organization_id: UUID | None,
            user_id: UUID,
            model_name: str | None = None,
            pod_id: UUID | None = None,
            require_vision: bool = False,
        ) -> ResolvedAgentRuntime | None:
            asked.update(
                organization_id=organization_id,
                pod_id=pod_id,
                model_name=model_name,
                require_vision=require_vision,
            )
            return resolved

        pod_id = uuid4()
        result = await resolve_system_or_workspace_runtime(
            organization_id=_ORG,
            user_id=_USER,
            model_name="title-model",
            pod_id=pod_id,
            workspace_runtime=workspace_runtime,
        )

        assert result is resolved
        assert asked == {
            "organization_id": _ORG,
            "pod_id": pod_id,
            "model_name": "title-model",
            "require_vision": False,
        }

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("no_system_model")
    async def test_nothing_anywhere_is_still_model_not_configured(self) -> None:
        async def nothing(**_: object) -> ResolvedAgentRuntime | None:
            return None

        with pytest.raises(DomainError) as caught:
            await resolve_system_or_workspace_runtime(
                organization_id=_ORG, user_id=_USER, workspace_runtime=nothing
            )

        assert caught.value.code == "model_not_configured"
        assert caught.value.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("no_system_model")
    async def test_without_an_organization_there_is_no_workspace_to_ask(
        self,
    ) -> None:
        with pytest.raises(DomainError) as caught:
            await resolve_system_or_workspace_runtime(
                organization_id=None, user_id=_USER
            )

        assert caught.value.code == "model_not_configured"

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("no_system_model")
    async def test_a_configured_system_model_is_used_as_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LEMMA_OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("LEMMA_OPENAI_MODEL_NAMES", "system-one,system-two")
        monkeypatch.setenv("LEMMA_OPENAI_DEFAULT_MODEL", "system-one")

        async def never(**_: object) -> ResolvedAgentRuntime | None:
            raise AssertionError("the workspace must not be asked")

        result = await resolve_system_or_workspace_runtime(
            organization_id=_ORG,
            user_id=_USER,
            model_name="system-two",
            workspace_runtime=never,
        )

        assert result.profile.id == "system:lemma"
        assert result.model is not None and result.model.name == "system-two"
