"""The system OpenAI catalog customizer hook.

The core builds the system OpenAI-compatible catalog from env (public name ==
provider model name, no vision). A provider overlay can register a customizer to
remap provider model IDs and declare per-model vision while keeping the short
public names. These tests pin that seam and its default (no-op) behavior.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
)
from app.modules.agent.services import runtime_profile_service
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
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


def test_customizer_remaps_provider_and_vision(openai_env):
    mapping = {
        "minimax-m3": ("accounts/fireworks/models/minimax-m3", True),
        "glm-5.2": ("accounts/fireworks/models/glm-5p2", False),
    }

    def customizer(entries):
        out = []
        for entry in entries:
            provider, vision = mapping.get(entry.name, (entry.provider_model_name, False))
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
            e.model_copy(update={"provider_model_name": "accounts/x/y"}) for e in entries
        ]
    )
    register_system_openai_catalog_customizer(None)
    catalog = _system_catalog()
    assert catalog["minimax-m3"].provider_model_name == "minimax-m3"
    assert runtime_profile_service._system_openai_catalog_customizer is None
