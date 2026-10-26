"""The one runtime resolution chain, as table cases.

Each case is one step of the chain in ``services/runtime_resolution``: the
workspace model a background call falls back to on a deployment with no system
model, the organization's default for an unpinned teammate, the in-process
model a stranger's turn is moved onto, and the system default a local checkout
may name in a file.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.outsiders import Audience, OutsiderRunRefused
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime
from app.modules.agent.domain.runtime_choice import (
    RuntimePurpose,
    choose_organization_runtime,
    choose_workspace_runtime,
)
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig, HarnessKind
from app.modules.agent.agent_host.harness.remote_payload import mcp_payload
from app.modules.agent.services.runtime_resolution import (
    RuntimeRequest,
    in_process_runtime,
    local_default_runtime,
    resolve_runtime,
    resolve_system_or_workspace_runtime,
)

pytestmark = pytest.mark.unit

_ORG = uuid4()
_USER = uuid4()
_TEXT = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
_SEES = [*_TEXT, RuntimeModelCapability.VISION]


def _entry(
    name: str, capabilities: list[RuntimeModelCapability] = _TEXT
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
        id=name,
        organization_id=_ORG,
        user_id=_USER if scope is RuntimeProfileScope.PERSONAL else None,
        scope=scope,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name=name,
        default_model_name=entries[0].name,
        model_catalog=list(entries),
        config={"base_url": "https://models.example.test/v1"},
    )


def _harness() -> AgentRuntimeProfile:
    return AgentRuntimeProfile(
        id="harness",
        organization_id=_ORG,
        user_id=_USER,
        harness_id=uuid4(),
        scope=RuntimeProfileScope.PERSONAL,
        kind=RuntimeProfileKind.HARNESS,
        protocol=RuntimeProfileProtocol.AGENT_HOST,
        name="A coding agent",
        model_catalog=[_entry("agent-model", _SEES)],
        config={"harness_snapshot_revision": "rev-1"},
    )


ALPHA = _provider("alpha", _entry("alpha-1"), _entry("alpha-2"))
BETA = _provider("beta", _entry("beta-1"), _entry("beta-2"))
MINE = _provider("mine", _entry("mine-1"), scope=RuntimeProfileScope.PERSONAL)
TEXT_ONLY = _provider("text", _entry("words"))
MIXED = _provider("mixed", _entry("words-too"), _entry("eyes", _SEES))


def _config(profile: str, model: str) -> AgentRuntimeConfig:
    return AgentRuntimeConfig(profile_id=profile, model_name=model)


# (case, profiles, pod default, configured name, require vision, expected)
WORKSPACE_CASES = [
    (
        "the pod's own default wins",
        [ALPHA, BETA],
        _config("beta", "beta-2"),
        None,
        False,
        _config("beta", "beta-2"),
    ),
    (
        "organization providers come before personal ones",
        [MINE, ALPHA],
        None,
        None,
        False,
        _config("alpha", "alpha-1"),
    ),
    ("a coding agent is never picked", [_harness()], None, None, False, None),
    (
        "a configured name is used when the provider serves it",
        [ALPHA],
        None,
        "alpha-2",
        False,
        _config("alpha", "alpha-2"),
    ),
    (
        "an unknown configured name falls back to the provider default",
        [ALPHA],
        None,
        "elsewhere",
        False,
        _config("alpha", "alpha-1"),
    ),
    (
        "vision only settles for a model that reads images",
        [TEXT_ONLY, MIXED],
        None,
        None,
        True,
        _config("mixed", "eyes"),
    ),
    (
        "no image-reading model means none, not a text model",
        [TEXT_ONLY],
        None,
        None,
        True,
        None,
    ),
]


@pytest.mark.parametrize(
    ("profiles", "pod_default", "model_name", "require_vision", "expected"),
    [case[1:] for case in WORKSPACE_CASES],
    ids=[case[0] for case in WORKSPACE_CASES],
)
def test_the_workspace_model_a_background_call_falls_back_to(
    profiles, pod_default, model_name, require_vision, expected
) -> None:
    assert (
        choose_workspace_runtime(
            profiles,
            pod_default=pod_default,
            model_name=model_name,
            require_vision=require_vision,
        )
        == expected
    )


ORGANIZATION_CASES = [
    (
        "the first organization provider on its own default",
        [ALPHA, BETA],
        _config("alpha", "alpha-1"),
    ),
    ("a member's personal key never becomes everyone's", [MINE, _harness()], None),
]


@pytest.mark.parametrize(
    ("profiles", "expected"),
    [case[1:] for case in ORGANIZATION_CASES],
    ids=[case[0] for case in ORGANIZATION_CASES],
)
def test_the_organizations_default_on_a_deployment_with_no_model(
    profiles, expected
) -> None:
    assert choose_organization_runtime(profiles) == expected


# -- A stranger's turn ----------------------------------------------------------


def _runtime(kind: HarnessKind, scope: RuntimeProfileScope) -> SimpleNamespace:
    return SimpleNamespace(harness_kind=kind, profile=SimpleNamespace(scope=scope))


AGENT_HOST = _runtime(HarnessKind.HARNESS, RuntimeProfileScope.ORGANIZATION)
IN_PROCESS = _runtime(HarnessKind.LEMMA, RuntimeProfileScope.ORGANIZATION)
#: A member's own model: runs here, but billed to them.
PERSONAL = _runtime(HarnessKind.LEMMA, RuntimeProfileScope.PERSONAL)
ORGANIZATION = AgentRuntimeConfig(profile_id="organization-default")
SYSTEM = AgentRuntimeConfig(profile_id="system-default")


def _resolving(answers: dict[str, object]):
    async def resolve(config: AgentRuntimeConfig):
        answer = answers[config.profile_id]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return resolve


# (case, the run's runtime, fallbacks, what each fallback resolves to, chosen)
OUTSIDER_CASES = [
    (
        "a run that already runs here keeps its runtime",
        IN_PROCESS,
        [ORGANIZATION],
        {},
        IN_PROCESS,
    ),
    (
        "a run bound for Agent Host takes the first default that runs here",
        AGENT_HOST,
        [ORGANIZATION, SYSTEM],
        {"organization-default": AGENT_HOST, "system-default": IN_PROCESS},
        IN_PROCESS,
    ),
    (
        "a default that cannot be resolved is passed over",
        AGENT_HOST,
        [ORGANIZATION, SYSTEM],
        {"organization-default": DomainError("retired"), "system-default": IN_PROCESS},
        IN_PROCESS,
    ),
    (
        "a member's personal model is never spent on a stranger",
        PERSONAL,
        [ORGANIZATION, SYSTEM],
        {"organization-default": PERSONAL, "system-default": IN_PROCESS},
        IN_PROCESS,
    ),
    (
        "with nothing that runs here the stranger is refused",
        AGENT_HOST,
        [ORGANIZATION],
        {"organization-default": AGENT_HOST},
        OutsiderRunRefused,
    ),
    (
        "with only a personal model the stranger is refused",
        PERSONAL,
        [ORGANIZATION],
        {"organization-default": PERSONAL},
        OutsiderRunRefused,
    ),
]


@pytest.mark.parametrize(
    ("resolved", "fallbacks", "answers", "chosen"),
    [case[1:] for case in OUTSIDER_CASES],
    ids=[case[0] for case in OUTSIDER_CASES],
)
async def test_a_strangers_turn_only_runs_in_this_process(
    resolved, fallbacks, answers, chosen
) -> None:
    if chosen is OutsiderRunRefused:
        with pytest.raises(OutsiderRunRefused):
            await in_process_runtime(
                resolved, fallbacks=fallbacks, resolve=_resolving(answers)
            )
        return
    assert (
        await in_process_runtime(
            resolved, fallbacks=fallbacks, resolve=_resolving(answers)
        )
        is chosen
    )


async def test_each_fallback_that_cannot_be_resolved_says_why(caplog) -> None:
    """A refusal tells the person only that there is no model; the reason each
    candidate fell through has to be somewhere an operator can read it."""
    with caplog.at_level("WARNING"), pytest.raises(OutsiderRunRefused):
        await in_process_runtime(
            AGENT_HOST,
            fallbacks=[ORGANIZATION, SYSTEM],
            resolve=_resolving(
                {
                    "organization-default": DomainError(
                        "retired", code="MODEL_RETIRED"
                    ),
                    "system-default": RuntimeError("no key"),
                }
            ),
        )

    skipped = [
        record.getMessage()
        for record in caplog.records
        if "agent.outsider_runtime.fallback_skipped.degraded" in record.getMessage()
    ]
    assert len(skipped) == 2
    assert "MODEL_RETIRED" in skipped[0] and "organization-default" in skipped[0]
    assert "RuntimeError" in skipped[1]


async def test_a_members_turn_keeps_its_own_runtime_through_the_one_chain() -> None:
    asked: list[AgentRuntimeConfig] = []

    async def resolve(config: AgentRuntimeConfig):
        asked.append(config)
        return AGENT_HOST

    pinned = _config("alpha", "alpha-2")
    chosen = await resolve_runtime(
        RuntimeRequest(organization_id=_ORG, user_id=_USER, configured=pinned),
        purpose=RuntimePurpose.TURN,
        resolve=resolve,
    )

    assert chosen is AGENT_HOST
    assert asked == [pinned]


class _NoTokens:
    """The workspace service that would mint the run's Lemma token."""

    async def get_env_vars(self, **_kwargs):
        raise AssertionError("a token was minted for a stranger's run")

    async def close(self) -> None:
        return None


async def test_agent_host_is_never_handed_a_token_for_a_strangers_run() -> None:
    stranger_run = SimpleNamespace(
        audience=Audience.contact(uuid4()),
        user_id=uuid4(),
        pod_id=uuid4(),
        org_id=uuid4(),
        agent_name="pod_default",
    )

    with pytest.raises(OutsiderRunRefused):
        await mcp_payload(
            agent_run_id=uuid4(),
            conversation_id=uuid4(),
            ctx=stranger_run,
            options=SimpleNamespace(),
            workspace_service=_NoTokens(),
        )


# -- The system default ---------------------------------------------------------


# (case, environment, file contents or None for no file, expected profile id)
LOCAL_DEFAULT_CASES = [
    (
        "a local checkout with no file runs on the system model",
        "local",
        None,
        "system:lemma",
    ),
    (
        "a local checkout may name another runtime in a file",
        "local",
        '{"profile_id": "user-profile"}',
        "user-profile",
    ),
    (
        "an unreadable file is the system model",
        "local",
        "not json",
        "system:lemma",
    ),
    (
        "only a local checkout reads the file",
        "development",
        '{"profile_id": "user-profile"}',
        "system:lemma",
    ),
]


@pytest.mark.parametrize(
    ("environment", "contents", "expected"),
    [case[1:] for case in LOCAL_DEFAULT_CASES],
    ids=[case[0] for case in LOCAL_DEFAULT_CASES],
)
def test_the_system_default(tmp_path, environment, contents, expected) -> None:
    path = tmp_path / "agent-runtime.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")

    runtime = local_default_runtime(environment=environment, config_path=path)

    assert runtime.profile_id == expected


def test_the_system_default_follows_the_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        agent_settings,
        "local_agent_runtime_config_path",
        str(tmp_path / "missing-runtime.json"),
    )
    for environment in ("local", "development"):
        monkeypatch.setattr(settings, "environment", environment)
        assert local_default_runtime().profile_id == "system:lemma"


# -- A background call on a deployment with no system model ------------------------


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


def _resolved(profile: AgentRuntimeProfile) -> ResolvedAgentRuntime:
    return ResolvedAgentRuntime(
        profile=profile,
        harness_kind=profile.derived_harness_kind(),
        model=profile.model_catalog[0],
        provider_model_name=profile.model_catalog[0].name,
        credentials=None,
    )


@pytest.mark.usefixtures("no_system_model")
async def test_no_system_model_falls_back_to_the_workspace() -> None:
    resolved = _resolved(ALPHA)
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


@pytest.mark.usefixtures("no_system_model")
async def test_nothing_anywhere_is_still_model_not_configured() -> None:
    async def nothing(**_: object) -> ResolvedAgentRuntime | None:
        return None

    with pytest.raises(DomainError) as caught:
        await resolve_system_or_workspace_runtime(
            organization_id=_ORG, user_id=_USER, workspace_runtime=nothing
        )

    assert caught.value.code == "model_not_configured"
    assert caught.value.status_code == 503


@pytest.mark.usefixtures("no_system_model")
async def test_without_an_organization_there_is_no_workspace_to_ask() -> None:
    with pytest.raises(DomainError) as caught:
        await resolve_system_or_workspace_runtime(organization_id=None, user_id=_USER)

    assert caught.value.code == "model_not_configured"


@pytest.mark.usefixtures("no_system_model")
async def test_a_configured_system_model_is_used_as_before(
    monkeypatch: pytest.MonkeyPatch,
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


@pytest.mark.usefixtures("no_system_model")
@pytest.mark.parametrize("purpose", [RuntimePurpose.COMPACTION, RuntimePurpose.MEMORY])
async def test_a_side_job_without_a_system_model_stays_on_the_runs_model(
    purpose: RuntimePurpose,
) -> None:
    """``None`` is the run's own model, for the purposes that fall back to it."""
    for model_name in (None, "small-model"):
        assert (
            await resolve_runtime(
                RuntimeRequest(
                    organization_id=_ORG, user_id=_USER, model_name=model_name
                ),
                purpose=purpose,
            )
            is None
        )
