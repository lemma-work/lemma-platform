"""The system OpenAI-compatible catalog, built from the environment.

Public name == provider model name, no vision unless declared. (The runtime
customizer hook that could remap it was never registered in production and is
gone.)
"""

from __future__ import annotations

import pytest

from app.modules.agent.config import agent_settings
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
)
from app.modules.agent.services import runtime_system_profiles as system_profiles
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
)
from app.modules.agent.services.runtime_system_profiles import (
    system_lemma_openai_catalog_model_names,
)


@pytest.fixture
def openai_env(monkeypatch):
    monkeypatch.setenv("LEMMA_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LEMMA_OPENAI_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv("LEMMA_OPENAI_MODEL_NAMES", "minimax-m3,glm-5.2")
    monkeypatch.setenv("LEMMA_OPENAI_DEFAULT_MODEL", "minimax-m3")
    monkeypatch.setenv("LEMMA_DEFAULT_MODEL_TYPE", "openai_compat")


def _system_catalog() -> dict[str, RuntimeModelCatalogEntry]:
    profiles = AgentRuntimeProfileService().system_profiles()
    assert profiles, "system profile should exist when the API key is set"
    return {entry.name: entry for entry in profiles[0].model_catalog}


def test_default_catalog_uses_names_verbatim(openai_env):
    catalog = _system_catalog()
    assert set(catalog) == {"minimax-m3", "glm-5.2"}
    for name, entry in catalog.items():
        # No customizer: public name == provider model name, no vision.
        assert entry.provider_model_name == name
        assert RuntimeModelCapability.VISION not in entry.capabilities


def test_openai_compat_profile_replaces_rather_than_sums_streamed_usage(openai_env):
    """pydantic-ai's OpenAI streaming handler defaults to `usage += chunk_usage`
    per SSE chunk -- correct only when a provider sends usage on a single final
    chunk. A provider that repeats an already-cumulative total on every chunk is
    then summed on top of itself, and the turn bills a multiple of what it used.
    `openai_continuous_usage_stats` switches pydantic-ai to replace, which is
    correct under both conventions. Set at the profile level, not per model, so
    it covers every model behind this provider.

    Its other half must not reach the wire -- see the stream-options test
    below."""
    profiles = AgentRuntimeProfileService().system_profiles()
    assert profiles, "system profile should exist when the API key is set"
    config = profiles[0].config
    assert config.model_settings.get("openai_continuous_usage_stats") is True


def test_continuous_usage_stats_never_reaches_the_request(openai_env):
    """The setting above selects an accumulation rule, and that is all it may do
    here.

    pydantic-ai spends the same flag twice: it also puts
    `continuous_usage_stats` into the request's `stream_options`, which is a
    vLLM extension and not in the OpenAI schema -- the openai SDK's own
    stream-options type has no such field. An endpoint that validates its input
    rejects the whole request, so a model that would otherwise work cannot
    stream at all. Models behind a single provider disagree about accepting it,
    and this profile fronts whatever endpoint an operator configures, so the
    field is never safe to send.

    Nothing is given up by withholding it: a provider that reports cumulative
    usage does so unprompted, and the field cannot switch that off. So this
    asserts the pair -- the setting is on the profile, and the request carries
    only what OpenAI defines."""
    from app.modules.agent.infrastructure.providers.model_factory import (
        require_pydantic_ai_model_from_runtime_profile,
    )

    profiles = AgentRuntimeProfileService().system_profiles()
    assert profiles, "system profile should exist when the API key is set"
    profile = profiles[0]
    model = require_pydantic_ai_model_from_runtime_profile(
        runtime_profile={
            "profile_id": profile.id,
            "protocol": "OPENAI_COMPATIBLE",
            "config": profile.config.model_dump(mode="json"),
            "provider_model_name": profile.default_model_name,
        },
        runtime_credentials={"api_key": "test-key"},
    )

    model_settings = profile.config.model_settings
    assert model_settings.get("openai_continuous_usage_stats") is True

    # The settings that would make stock pydantic-ai emit the field.
    stream_options = model._get_stream_options(model_settings)
    assert stream_options == {"include_usage": True}
    assert "continuous_usage_stats" not in stream_options


def test_pricing_catalog_is_empty_when_no_models_are_configured(monkeypatch):

    monkeypatch.setattr(system_profiles, "_load_runtime_env", lambda: None)
    monkeypatch.setattr(agent_settings, "lemma_openai_model_names", "")
    monkeypatch.setattr(agent_settings, "lemma_openai_default_model", "")
    monkeypatch.delenv("LEMMA_OPENAI_MODEL_NAMES", raising=False)
    monkeypatch.delenv("LEMMA_OPENAI_DEFAULT_MODEL", raising=False)

    assert system_lemma_openai_catalog_model_names() == []
