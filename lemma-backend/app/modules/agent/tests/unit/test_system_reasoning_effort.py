"""The deployment's reasoning effort: configuration, read once, gated per model.

`LEMMA_OPENAI_REASONING_EFFORT` used to be read with `os.getenv` on every
profile build, after re-reading two `.env` files from disk, and written into the
profile's settings for every model whether it reasoned or not. It is now an
`AgentSettings` field that becomes a catalog default on the models that reason.
"""

from __future__ import annotations

import pytest

from app.core.domain.generation import ReasoningEffort
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCatalogEntry,
)
from app.modules.agent.services import runtime_system_profiles as system_profiles
from app.modules.agent.services.runtime_system_profiles import (
    system_lemma_profile,
)


@pytest.fixture(autouse=True)
def openai_env(monkeypatch):
    monkeypatch.setenv("LEMMA_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LEMMA_OPENAI_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv("LEMMA_OPENAI_MODEL_NAMES", "thinker,talker")
    monkeypatch.setenv("LEMMA_OPENAI_DEFAULT_MODEL", "thinker")
    monkeypatch.setenv("LEMMA_DEFAULT_MODEL_TYPE", "openai_compat")
    monkeypatch.setattr(agent_settings, "lemma_openai_reasoning_effort", None)
    monkeypatch.setattr(agent_settings, "lemma_openai_reasoning_model_names", "")
    yield


def _catalog() -> dict[str, RuntimeModelCatalogEntry]:
    profile = system_lemma_profile()
    assert profile is not None
    return {entry.name: entry for entry in profile.model_catalog}


def test_unset_declares_nothing_and_sends_nothing():
    profile = system_lemma_profile()
    assert profile is not None

    assert profile.config.model_settings == {"openai_continuous_usage_stats": True}
    assert not any(entry.reasons for entry in profile.model_catalog)


def test_an_effort_alone_covers_every_model_as_before(monkeypatch):
    """Before, the effort reached every model; setting it still means that."""
    monkeypatch.setattr(
        agent_settings, "lemma_openai_reasoning_effort", ReasoningEffort.LOW
    )

    for entry in _catalog().values():
        assert entry.reasons
        assert entry.default_model_settings == {"reasoning_effort": "low"}


def test_named_reasoning_models_narrow_who_gets_it(monkeypatch):
    monkeypatch.setattr(
        agent_settings, "lemma_openai_reasoning_effort", ReasoningEffort.LOW
    )
    monkeypatch.setattr(agent_settings, "lemma_openai_reasoning_model_names", "thinker")

    catalog = _catalog()

    assert catalog["thinker"].reasons
    assert catalog["thinker"].default_model_settings == {"reasoning_effort": "low"}
    assert not catalog["talker"].reasons
    assert catalog["talker"].default_model_settings == {}


def test_building_the_profile_reads_no_env_file_after_the_first_time(monkeypatch):
    reads: list[object] = []
    monkeypatch.setattr(system_profiles, "_runtime_env_loaded", False)
    monkeypatch.setattr(
        system_profiles, "dotenv_values", lambda path: reads.append(path) or {}
    )

    system_lemma_profile()
    first = len(reads)
    system_lemma_profile()
    system_lemma_profile()

    assert first > 0
    assert len(reads) == first
