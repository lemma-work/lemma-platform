"""The organization's chosen model: the mark, where it ranks, and the Test button.

The mark lives in a profile's metadata, so everything that decides what it
means is pure and is pinned here without a database; the writes that keep it on
at most one profile are covered end to end in
`tests/e2e/test_organization_default_runtime_e2e.py`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.modules.agent.domain.organization_default import (
    ORGANIZATION_DEFAULT_KEY,
    can_be_organization_default,
    organization_default_of,
    with_default_mark,
    without_default_mark,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig, HarnessKind
from app.modules.agent.services.runtime_profile_service import ResolvedAgentRuntime
from app.modules.agent.services.runtime_provider_check import (
    ProviderCheckCollaborators,
    ProviderCheckResult,
    check_saved_provider_connection,
)
from app.modules.agent.services.runtime_provider_discovery import (
    DiscoveredModel,
    ProviderKeyRejectedError,
    ProviderListingError,
    ProviderUnreachableError,
)
from app.modules.agent.services.workspace_model_fallback import (
    choose_organization_runtime,
    choose_workspace_runtime,
)

pytestmark = pytest.mark.unit

_ORG = uuid4()
_USER = uuid4()
_TEXT = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]


def _entry(name: str) -> RuntimeModelCatalogEntry:
    return RuntimeModelCatalogEntry(
        name=name, provider_model_name=name, capabilities=_TEXT
    )


def _provider(
    name: str,
    *models: str,
    scope: RuntimeProfileScope = RuntimeProfileScope.ORGANIZATION,
    status: RuntimeProfileStatus = RuntimeProfileStatus.ACTIVE,
    base_url: str = "https://models.example.test/v1",
) -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=_ORG,
        user_id=_USER if scope is RuntimeProfileScope.PERSONAL else None,
        scope=scope,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name=name,
        default_model_name=models[0],
        model_catalog=[_entry(model) for model in models],
        config={"base_url": base_url},
        credentials=ApiKeyRuntimeCredentials(api_key="sk-test-not-real"),
        status=status,
    )


def _harness() -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=_ORG,
        user_id=_USER,
        harness_id=uuid4(),
        scope=RuntimeProfileScope.ORGANIZATION,
        kind=RuntimeProfileKind.HARNESS,
        protocol=RuntimeProfileProtocol.AGENT_HOST,
        name="A coding agent",
        model_catalog=[_entry("agent-model")],
    )


class TestWhoCanBeTheDefault:
    def test_an_active_organization_provider_can(self) -> None:
        assert can_be_organization_default(_provider("Alpha", "a-1"))

    @pytest.mark.parametrize(
        "profile",
        [
            pytest.param(
                _provider("Mine", "m-1", scope=RuntimeProfileScope.PERSONAL),
                id="a member's personal key",
            ),
            pytest.param(
                _provider("Old", "o-1", status=RuntimeProfileStatus.DISABLED),
                id="an archived provider",
            ),
            pytest.param(_harness(), id="a coding agent"),
        ],
    )
    def test_nothing_else_can(self, profile: AgentRuntimeProfile) -> None:
        assert not can_be_organization_default(profile)
        with pytest.raises(ValueError, match="organization-wide model provider"):
            with_default_mark(profile, model_name=None)

    def test_a_model_the_provider_does_not_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="does not offer"):
            with_default_mark(_provider("Alpha", "a-1"), model_name="a-9")


class TestWhatTheMarkResolvesTo:
    def test_the_chosen_model(self) -> None:
        marked = with_default_mark(_provider("Alpha", "a-1", "a-2"), model_name="a-2")

        assert organization_default_of([marked]) == AgentRuntimeConfig(
            profile_id=marked.id, model_name="a-2"
        )

    def test_no_model_follows_the_providers_own_default(self) -> None:
        marked = with_default_mark(_provider("Alpha", "a-1", "a-2"), model_name=None)

        assert organization_default_of([marked]) == AgentRuntimeConfig(
            profile_id=marked.id, model_name="a-1"
        )

    def test_a_model_the_provider_dropped_falls_to_its_default(self) -> None:
        marked = with_default_mark(_provider("Alpha", "a-1", "a-2"), model_name="a-2")
        dropped = marked.with_changes(model_catalog=[_entry("a-1")])

        assert organization_default_of([dropped]) == AgentRuntimeConfig(
            profile_id=marked.id, model_name="a-1"
        )

    def test_a_mark_on_an_archived_profile_is_ignored(self) -> None:
        marked = with_default_mark(_provider("Alpha", "a-1"), model_name=None)
        archived = marked.with_changes(status=RuntimeProfileStatus.DISABLED)

        assert organization_default_of([archived]) is None

    def test_removing_the_mark_keeps_the_rest_of_the_metadata(self) -> None:
        marked = with_default_mark(
            _provider("Alpha", "a-1").with_changes(
                metadata={"catalog_discovered": True}
            ),
            model_name=None,
        )

        cleared = without_default_mark(marked)

        assert ORGANIZATION_DEFAULT_KEY not in cleared.metadata
        assert cleared.metadata == {"catalog_discovered": True}


class TestWhereTheDefaultRanks:
    def test_it_wins_over_the_first_provider(self) -> None:
        first = _provider("Alpha", "a-1")
        chosen = with_default_mark(_provider("Beta", "b-1", "b-2"), model_name="b-2")

        assert choose_organization_runtime([first, chosen]) == AgentRuntimeConfig(
            profile_id=chosen.id, model_name="b-2"
        )

    def test_it_wins_over_a_server_model(self) -> None:
        chosen = with_default_mark(_provider("Beta", "b-1"), model_name=None)

        assert choose_organization_runtime(
            [chosen], server_has_model=True
        ) == AgentRuntimeConfig(profile_id=chosen.id, model_name="b-1")

    def test_the_first_provider_guess_never_beats_a_server_model(self) -> None:
        assert (
            choose_organization_runtime(
                [_provider("Alpha", "a-1")], server_has_model=True
            )
            is None
        )

    def test_background_calls_try_it_right_after_the_pods_default(self) -> None:
        first = _provider("Alpha", "a-1")
        chosen = with_default_mark(_provider("Beta", "b-1", "b-2"), model_name="b-2")
        pinned = _provider("Gamma", "g-1")

        assert choose_workspace_runtime(
            [first, chosen, pinned],
            pod_default=None,
            model_name=None,
            require_vision=False,
        ) == AgentRuntimeConfig(profile_id=chosen.id, model_name="b-2")
        assert choose_workspace_runtime(
            [first, chosen, pinned],
            pod_default=AgentRuntimeConfig(profile_id=pinned.id),
            model_name=None,
            require_vision=False,
        ) == AgentRuntimeConfig(profile_id=pinned.id, model_name="g-1")


def _resolved(profile: AgentRuntimeProfile) -> ResolvedAgentRuntime:
    model = profile.model_catalog[0]
    return ResolvedAgentRuntime(
        profile=profile,
        harness_kind=HarnessKind.LEMMA,
        model=model,
        provider_model_name=model.provider_model_name,
        credentials={"api_key": "sk-test-not-real"},
    )


def _replies_ok() -> ModelResponse:
    return ModelResponse(parts=[TextPart("OK")])


async def _lists_two(**_kwargs) -> list[DiscoveredModel]:
    return [DiscoveredModel("a-1"), DiscoveredModel("a-2")]


def _provider_that(
    *,
    lists: Callable[..., Awaitable[list[DiscoveredModel]]] = _lists_two,
    answers: Callable[[], ModelResponse] = _replies_ok,
) -> ProviderCheckCollaborators:
    """A provider standing in at the check's own seams, not patched into it."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return answers()

    return ProviderCheckCollaborators(
        list_openai_compatible=lists,
        build_model=lambda **_kwargs: FunctionModel(respond),
    )


async def _check(
    collaborators: ProviderCheckCollaborators, name: str = "Alpha"
) -> ProviderCheckResult:
    return await check_saved_provider_connection(
        _resolved(_provider(name, "a-1")), collaborators=collaborators
    )


class TestTestingASavedProvider:
    async def test_a_working_provider_names_its_model_and_lists_the_rest(
        self,
    ) -> None:
        result = await _check(_provider_that())

        assert result == ProviderCheckResult(
            ok=True, message="Alpha answered using a-1.", models=["a-1", "a-2"]
        )

    async def test_a_rejected_key_at_the_listing(self) -> None:
        async def rejected(**_kwargs) -> list[DiscoveredModel]:
            raise ProviderKeyRejectedError("401")

        result = await _check(_provider_that(lists=rejected))

        assert result == ProviderCheckResult(
            ok=False, message="Alpha rejected this API key."
        )

    async def test_a_local_server_that_is_not_running(self) -> None:
        async def refused(**_kwargs) -> list[DiscoveredModel]:
            raise ProviderUnreachableError("Ollama")

        result = await _check(_provider_that(lists=refused))

        assert result == ProviderCheckResult(
            ok=False, message="Ollama isn't running on this computer."
        )

    async def test_an_unreachable_remote_provider(self) -> None:
        async def unreachable(**_kwargs) -> list[DiscoveredModel]:
            raise ProviderUnreachableError(None)

        result = await _check(_provider_that(lists=unreachable))

        assert result.message == "Couldn't reach Alpha. Check its address."

    async def test_an_unreadable_listing_is_decided_by_the_completion(self) -> None:
        async def unreadable(**_kwargs) -> list[DiscoveredModel]:
            raise ProviderListingError("404")

        result = await _check(_provider_that(lists=unreadable))

        assert result == ProviderCheckResult(
            ok=True, message="Alpha answered using a-1.", models=None
        )

    async def test_a_key_rejected_by_the_completion(self) -> None:
        def refuse() -> ModelResponse:
            raise ModelHTTPError(
                status_code=401, model_name="a-1", body="provider says: bad key abc"
            )

        result = await _check(_provider_that(answers=refuse))

        assert result.message == "Alpha rejected this API key."

    async def test_the_providers_own_words_never_reach_the_page(self) -> None:
        def fail() -> ModelResponse:
            raise ModelHTTPError(
                status_code=500, model_name="a-1", body="internal: secret-detail"
            )

        result = await _check(_provider_that(answers=fail))

        assert result == ProviderCheckResult(
            ok=False, message="Alpha didn't answer a test message on a-1."
        )

    async def test_a_refused_local_completion_names_the_server(self) -> None:
        def refuse() -> ModelResponse:
            raise httpx.ConnectError(
                "refused",
                request=httpx.Request("POST", "http://127.0.0.1:1234/v1/chat"),
            )

        result = await _check(_provider_that(answers=refuse), name="Studio")

        assert result.message == "LM Studio isn't running on this computer."

    async def test_a_coding_agent_is_not_tested_here(self) -> None:
        with pytest.raises(ValueError, match="Only a model provider"):
            await check_saved_provider_connection(
                ResolvedAgentRuntime(
                    profile=_harness(),
                    harness_kind=HarnessKind.HARNESS,
                    model=None,
                    provider_model_name=None,
                    credentials=None,
                )
            )
