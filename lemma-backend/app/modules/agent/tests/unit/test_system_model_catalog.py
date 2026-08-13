"""The built-in system model catalog behind the model picker (issue #168).

The picker lists the system profile's catalog, built from
LEMMA_OPENAI_MODEL_NAMES / LEMMA_ANTHROPIC_MODEL_NAMES with the declared
settings defaults underneath. These tests pin those defaults — the GPT-5.6
family (Sol / Terra / Luna) on the OpenAI-compatible side and Claude Fable 5
on the Anthropic-compatible side — plus the routing of each new ID to its
provider model and the reasoning-effort pass-through the GPT-5.6 family
relies on.

The bare ``gpt-5.6`` alias is deliberately not a picker entry: aliases drift
as providers repoint them, which would make run records and cost attribution
ambiguous. It still routes if an operator sets it manually — model IDs are
forwarded to the endpoint verbatim.
"""

from __future__ import annotations

import typing
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
    RuntimeProfileProtocol,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services import runtime_profile_service
from app.modules.agent.services.runtime_model_factory import (
    pydantic_ai_model_from_runtime_profile,
)
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    AgentRuntimeProfileService,
)

GPT_5_6_MODEL_NAMES = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
ANTHROPIC_MODEL_NAMES = ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-fable-5"]
NEW_MODEL_NAMES = [*GPT_5_6_MODEL_NAMES, "claude-fable-5"]
# The full effort set the GPT-5.6 family accepts.
GPT_5_6_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


def _declared_default(field_name: str) -> object:
    """The config field's declared default — immune to a developer's .env."""
    return Settings.model_fields[field_name].default


@pytest.fixture(autouse=True)
def _clear_customizer():
    runtime_profile_service.register_system_openai_catalog_customizer(None)
    yield
    runtime_profile_service.register_system_openai_catalog_customizer(None)


def _clear_openai_env(monkeypatch) -> None:
    # Keep the test hermetic: the profile builder reloads the local ``.env``
    # and prefers ``os.getenv`` over ``settings``, which would otherwise leak
    # the developer's real model list/credentials into this test.
    monkeypatch.setattr(runtime_profile_service, "_load_runtime_env", lambda: None)
    for name in (
        "LEMMA_DEFAULT_MODEL_TYPE",
        "LEMMA_OPENAI_API_KEY",
        "LEMMA_OPENAI_BASE_URL",
        "LEMMA_OPENAI_DEFAULT_MODEL",
        "LEMMA_OPENAI_MODEL_NAMES",
        "LEMMA_OPENAI_VISION_MODEL_NAMES",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def openai_key_only_env(monkeypatch):
    """A key-only OpenAI deployment: no model env, settings at defaults."""
    from app.core.config import settings

    _clear_openai_env(monkeypatch)
    monkeypatch.setattr(settings, "lemma_openai_api_key", "lemma-secret")
    monkeypatch.setattr(settings, "lemma_anthropic_api_key", None)
    for field in (
        "lemma_default_model_type",
        "lemma_openai_base_url",
        "lemma_openai_default_model",
        "lemma_openai_model_names",
        "lemma_openai_vision_model_names",
    ):
        monkeypatch.setattr(settings, field, _declared_default(field))


@pytest.fixture
def anthropic_key_only_env(monkeypatch):
    """A key-only Anthropic deployment: no model env, settings at defaults."""
    from app.core.config import settings

    _clear_openai_env(monkeypatch)
    monkeypatch.delenv("LEMMA_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LEMMA_ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("LEMMA_ANTHROPIC_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("LEMMA_ANTHROPIC_MODEL_NAMES", raising=False)
    monkeypatch.setenv("LEMMA_DEFAULT_MODEL_TYPE", "anthropic_compat")
    monkeypatch.setattr(settings, "lemma_default_model_type", "anthropic_compat")
    monkeypatch.setattr(settings, "lemma_openai_api_key", None)
    monkeypatch.setattr(settings, "lemma_anthropic_api_key", "lemma-anthropic-secret")
    for field in (
        "lemma_anthropic_base_url",
        "lemma_anthropic_default_model",
        "lemma_anthropic_model_names",
    ):
        monkeypatch.setattr(settings, field, _declared_default(field))


def test_default_openai_catalog_lists_the_gpt_5_6_family(openai_key_only_env):
    # The declared settings default is the built-in catalog; this assertion is
    # what fails if the config defaults regress.
    assert _declared_default("lemma_openai_model_names") == ",".join(
        GPT_5_6_MODEL_NAMES
    )

    profiles = AgentRuntimeProfileService().system_profiles()

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.protocol is RuntimeProfileProtocol.OPENAI_COMPATIBLE
    names = [model.name for model in profile.model_catalog]
    assert names == GPT_5_6_MODEL_NAMES
    # The bare provider alias is not a picker entry (aliases drift).
    assert "gpt-5.6" not in names
    # No configured default model: the flagship leads the catalog and the
    # profile build falls back to its first entry.
    assert profile.default_model_name == "gpt-5.6-sol"
    for model in profile.model_catalog:
        # The system profile uses model names verbatim.
        assert model.provider_model_name == model.name
        assert model.display_name == model.name.replace("-", " ").title()
        assert RuntimeModelCapability.TEXT in model.capabilities
        assert RuntimeModelCapability.TOOLS in model.capabilities
    # The picker payload (public profile view) carries the same entries.
    picker_catalog = profile.public_dict()["model_catalog"]
    assert [model["name"] for model in picker_catalog] == GPT_5_6_MODEL_NAMES


def test_default_anthropic_catalog_includes_claude_fable_5(anthropic_key_only_env):
    assert _declared_default("lemma_anthropic_model_names") == ",".join(
        ANTHROPIC_MODEL_NAMES
    )

    profiles = AgentRuntimeProfileService().system_profiles()

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE
    assert [model.name for model in profile.model_catalog] == ANTHROPIC_MODEL_NAMES
    fable = profile.model_catalog[-1]
    assert fable.name == "claude-fable-5"
    assert fable.display_name == "Claude Fable 5"
    assert fable.provider_model_name == "claude-fable-5"
    # Claude models are multimodal, so the Anthropic catalog declares VISION.
    assert RuntimeModelCapability.VISION in fable.capabilities
    # The configured default model is untouched by the new entry.
    assert profile.default_model_name == "claude-sonnet-4-5"


@pytest.mark.parametrize("model_name", NEW_MODEL_NAMES)
def test_agent_create_and_update_requests_accept_each_new_model(model_name):
    """The agent create/update payloads pin models through AgentRuntimeConfig,
    which validates the ID as a free-form string — no local enum to extend."""
    from app.modules.agent.api.schemas import CreateAgentRequest, UpdateAgentRequest

    runtime = AgentRuntimeConfig(
        profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
        model_name=model_name,
    )
    create = CreateAgentRequest(
        name="catalog-check", instruction="check", agent_runtime=runtime
    )
    update = UpdateAgentRequest(agent_runtime=runtime)

    assert create.agent_runtime is not None
    assert create.agent_runtime.model_name == model_name
    assert update.agent_runtime is not None
    assert update.agent_runtime.model_name == model_name


@pytest.mark.parametrize("model_name", GPT_5_6_MODEL_NAMES)
async def test_gpt_5_6_models_resolve_and_route_to_openai(
    openai_key_only_env, model_name
):
    """A run pinning a GPT-5.6 model ID builds an OpenAI request for that
    exact ID — the model name flows catalog -> resolve -> provider model
    unchanged."""
    service = AgentRuntimeProfileService()
    resolved = await service.resolve(
        runtime=AgentRuntimeConfig(
            profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
            model_name=model_name,
        ),
        organization_id=None,
        user_id=uuid4(),
    )

    assert resolved.profile.protocol is RuntimeProfileProtocol.OPENAI_COMPATIBLE
    assert resolved.provider_model_name == model_name

    model = pydantic_ai_model_from_runtime_profile(
        runtime_profile=resolved.public_snapshot(),
        runtime_credentials=resolved.credentials or {},
    )

    assert type(model).__name__ == "OpenAIChatModel"
    assert model.model_name == model_name


async def test_claude_fable_5_resolves_and_routes_to_anthropic(
    anthropic_key_only_env,
):
    service = AgentRuntimeProfileService()
    resolved = await service.resolve(
        runtime=AgentRuntimeConfig(
            profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
            model_name="claude-fable-5",
        ),
        organization_id=None,
        user_id=uuid4(),
    )

    assert resolved.profile.protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE
    assert resolved.provider_model_name == "claude-fable-5"

    model = pydantic_ai_model_from_runtime_profile(
        runtime_profile=resolved.public_snapshot(),
        runtime_credentials=resolved.credentials or {},
    )

    assert type(model).__name__ == "AnthropicModel"
    assert model.model_name == "claude-fable-5"


def _sdk_reasoning_effort_values() -> frozenset[str]:
    """The effort literals the pinned pydantic-ai SDK accepts."""
    from pydantic_ai.models.openai import OpenAIChatModelSettings

    effort_hint = typing.get_type_hints(OpenAIChatModelSettings)[
        "openai_reasoning_effort"
    ]
    values: set[str] = set()
    for arg in typing.get_args(effort_hint):
        if typing.get_origin(arg) is typing.Literal:
            values.update(typing.get_args(arg))
    return frozenset(values)


def test_pinned_sdk_accepts_the_gpt_5_6_effort_set():
    """Lemma hardcodes no reasoning-effort enum: profile ``model_settings``
    are a documented verbatim pass-through to pydantic-ai, so the accepted set
    is exactly what the pinned SDK types. Pin both directions — the full
    GPT-5.6 set (none/low/medium/high/xhigh/max) is accepted and a value
    outside the set is not."""
    accepted = _sdk_reasoning_effort_values()
    assert set(GPT_5_6_REASONING_EFFORTS) <= accepted
    # Pre-existing values keep validating (saved configs keep working)...
    assert {"minimal", "low", "medium", "high"} <= accepted
    # ...and the contract still rejects values outside the set.
    assert "extreme" not in accepted


@pytest.mark.parametrize(
    "model_name", [*GPT_5_6_MODEL_NAMES, "gpt-4o"]  # gpt-4o: a pre-existing model
)
@pytest.mark.parametrize("effort", GPT_5_6_REASONING_EFFORTS)
def test_reasoning_effort_passes_through_profile_config_unchanged(
    model_name, effort
):
    """Profile ``model_settings`` flow verbatim from the API schema into the
    runtime config pydantic-ai is built from — the same path for the new
    GPT-5.6 IDs as for existing models."""
    from app.modules.agent.api.schemas import (
        CreateOpenAICompatibleRuntimeProfileRequest,
    )
    from app.modules.agent.domain.runtime_profiles import (
        OpenAICompatibleRuntimeConfig,
    )

    assert effort in _sdk_reasoning_effort_values()

    model_settings = {"openai_reasoning_effort": effort}
    request = CreateOpenAICompatibleRuntimeProfileRequest(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model_name=model_name,
        model_names=[model_name],
        model_settings=model_settings,
    )
    assert request.model_settings == model_settings

    config = OpenAICompatibleRuntimeConfig(
        base_url="https://api.openai.com/v1",
        model_settings=request.model_settings,
    )
    assert config.model_settings == {"openai_reasoning_effort": effort}
