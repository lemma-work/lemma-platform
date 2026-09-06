"""The system OpenAI catalog customizer hook.

The core builds the system OpenAI-compatible catalog from env (public name ==
provider model name, no vision). A provider overlay can register a customizer to
remap provider model IDs and declare per-model vision while keeping the short
public names. These tests pin that seam and its default (no-op) behavior.
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
    register_system_openai_catalog_customizer,
    system_lemma_openai_catalog_model_names,
)


@pytest.fixture
def openai_env(monkeypatch):
    monkeypatch.setenv("LEMMA_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LEMMA_OPENAI_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv("LEMMA_OPENAI_MODEL_NAMES", "minimax-m3,glm-5.2")
    monkeypatch.setenv("LEMMA_OPENAI_DEFAULT_MODEL", "minimax-m3")
    monkeypatch.setenv("LEMMA_DEFAULT_MODEL_TYPE", "openai_compat")


@pytest.fixture(autouse=True)
def _clear_customizer():
    # Always start and end with no customizer so global state never leaks.
    register_system_openai_catalog_customizer(None)
    yield
    register_system_openai_catalog_customizer(None)


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
    from app.modules.agent.services.runtime_model_factory import (
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


def test_customizer_remaps_provider_and_vision(openai_env):
    mapping = {
        "minimax-m3": ("accounts/fireworks/models/minimax-m3", True),
        "glm-5.2": ("accounts/fireworks/models/glm-5p2", False),
    }

    def customizer(entries):
        out = []
        for entry in entries:
            provider, vision = mapping.get(
                entry.name, (entry.provider_model_name, False)
            )
            caps = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
            if vision:
                caps.append(RuntimeModelCapability.VISION)
            out.append(
                entry.model_copy(
                    update={"provider_model_name": provider, "capabilities": caps}
                )
            )
        return out

    register_system_openai_catalog_customizer(customizer)
    catalog = _system_catalog()

    # Public names stay; provider IDs + vision come from the customizer.
    assert set(catalog) == {"minimax-m3", "glm-5.2"}
    assert (
        catalog["minimax-m3"].provider_model_name
        == "accounts/fireworks/models/minimax-m3"
    )
    assert RuntimeModelCapability.VISION in catalog["minimax-m3"].capabilities
    assert RuntimeModelCapability.VISION not in catalog["glm-5.2"].capabilities

    # The pricing-coverage names reflect the same remap.
    pairs = dict(system_lemma_openai_catalog_model_names())
    assert pairs["minimax-m3"] == "accounts/fireworks/models/minimax-m3"
    assert pairs["glm-5.2"] == "accounts/fireworks/models/glm-5p2"


def test_public_dict_masks_remapped_provider_id(openai_env):
    """SYSTEM profiles hide the provider model ID behind the public name, so the
    remap never leaks to clients (the catalog name stays the identity)."""
    register_system_openai_catalog_customizer(
        lambda entries: [
            e.model_copy(update={"provider_model_name": f"accounts/x/{e.name}"})
            for e in entries
        ]
    )
    profiles = AgentRuntimeProfileService().system_profiles()
    payload = profiles[0].public_dict()
    for model in payload["model_catalog"]:
        assert model["provider_model_name"] == model["name"]


def test_clear_customizer_restores_default(openai_env):
    register_system_openai_catalog_customizer(
        lambda entries: [
            e.model_copy(update={"provider_model_name": "accounts/x/y"})
            for e in entries
        ]
    )
    register_system_openai_catalog_customizer(None)
    catalog = _system_catalog()
    assert catalog["minimax-m3"].provider_model_name == "minimax-m3"
    assert system_profiles._system_openai_catalog_customizer is None
