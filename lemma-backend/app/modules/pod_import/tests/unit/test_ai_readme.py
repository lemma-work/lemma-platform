"""Unit tests for the best-effort README polish pass. Every guard must resolve
to ``None`` (keep the deterministic draft) — publish never depends on a model."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.pod_import.infrastructure import ai_readme
from app.modules.pod_import.infrastructure.ai_readme import polish_available, polish_readme
from app.modules.pod_import.infrastructure.readme import IMPORT_BADGE_URL

# Long enough that the badge URL alone (~475 chars) sits well under half the
# draft — otherwise the too-short guard could never trip in the tests below.
_DRAFT = (
    "# Trumpet\n\n> A horn pod.\n\n"
    f"[![Import to Lemma]({IMPORT_BADGE_URL})](https://lemma.work/import/github/a/b)\n\n"
    "## What it does\n\n- Run 1 AI agent\n- Create 1 table, seed 2 rows\n\n"
    "## What's inside\n\n" + "| widgets | 2 | 2 |\n" * 100
)

_IDS = dict(user_id=uuid4(), organization_id=uuid4(), pod_id=uuid4())


def _allow_polish(monkeypatch):
    """Get past the mock-mode and profile guards without a real key/env."""
    from app.core import config
    from app.modules.agent.services import runtime_profile_service

    monkeypatch.setattr(config.settings, "e2e_llm_mode", "real")
    monkeypatch.setattr(runtime_profile_service, "_system_lemma_profile", lambda: object())


def _fake_model(monkeypatch, output_text: str):
    """Stub the resolve seam with a scripted model and neuter the usage layer
    (reserve/record open real DB sessions); returns the recorded statuses so a
    test can assert the run was billed."""
    from pydantic_ai.models.test import TestModel

    from app.modules.usage.services import pydantic_ai_tracking

    async def fake_resolve(**_kwargs):
        return TestModel(custom_output_text=output_text), {"profile_id": "system-lemma"}

    recorded: list[str] = []

    async def fake_reserve(**_kwargs):
        return None

    async def fake_record(*, status, **_kwargs):
        recorded.append(status)

    monkeypatch.setattr(ai_readme, "_resolve_system_model", fake_resolve)
    monkeypatch.setattr(pydantic_ai_tracking, "reserve_usage_for_runtime", fake_reserve)
    monkeypatch.setattr(pydantic_ai_tracking, "record_pydantic_ai_result_usage", fake_record)
    return recorded


@pytest.mark.asyncio
async def test_polish_is_skipped_in_mock_e2e_mode(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "e2e_llm_mode", "mock")
    assert await polish_readme(_DRAFT, pod_name="Trumpet", description=None, **_IDS) is None


@pytest.mark.asyncio
async def test_polish_is_skipped_when_no_system_profile_is_configured(monkeypatch):
    from app.core import config
    from app.modules.agent.services import runtime_profile_service

    monkeypatch.setattr(config.settings, "e2e_llm_mode", "real")
    monkeypatch.setattr(runtime_profile_service, "_system_lemma_profile", lambda: None)
    assert await polish_readme(_DRAFT, pod_name="Trumpet", description=None, **_IDS) is None


def test_a_misconfigured_profile_reads_as_unavailable_not_an_error(monkeypatch):
    """_system_lemma_profile raises (key set, no models) — preview/publish must
    see "off", not a 500."""
    from app.core import config
    from app.modules.agent.services import runtime_profile_service

    def boom():
        raise RuntimeError("Lemma system model profile requires at least one model")

    monkeypatch.setattr(config.settings, "e2e_llm_mode", "real")
    monkeypatch.setattr(runtime_profile_service, "_system_lemma_profile", boom)
    assert polish_available() is False


@pytest.mark.asyncio
async def test_polish_returns_the_models_output_and_bills_the_run(monkeypatch):
    _allow_polish(monkeypatch)
    polished_text = _DRAFT.replace("> A horn pod.", "> The horn pod that greets back.")
    recorded = _fake_model(monkeypatch, polished_text)
    assert await polish_readme(
        _DRAFT, pod_name="Trumpet", description="A horn pod.", **_IDS
    ) == polished_text.strip()
    assert recorded == ["COMPLETED"]


@pytest.mark.asyncio
async def test_a_whole_document_code_fence_is_unwrapped(monkeypatch):
    """The one instruction models most often ignore — left wrapped, GitHub
    renders the entire README as a literal code block."""
    _allow_polish(monkeypatch)
    _fake_model(monkeypatch, f"```markdown\n{_DRAFT}\n```")
    assert await polish_readme(
        _DRAFT, pod_name="Trumpet", description=None, **_IDS
    ) == _DRAFT.strip()


@pytest.mark.asyncio
async def test_polish_that_drops_the_badge_is_rejected(monkeypatch):
    _allow_polish(monkeypatch)
    _fake_model(monkeypatch, "Sure! Here's a much better README:\n\n# Trumpet\n" + "x" * len(_DRAFT))
    assert await polish_readme(_DRAFT, pod_name="Trumpet", description=None, **_IDS) is None


@pytest.mark.asyncio
async def test_polish_that_loses_most_of_the_document_is_rejected(monkeypatch):
    _allow_polish(monkeypatch)
    _fake_model(monkeypatch, f"[badge]({IMPORT_BADGE_URL})")
    assert await polish_readme(_DRAFT, pod_name="Trumpet", description=None, **_IDS) is None


@pytest.mark.asyncio
async def test_a_model_error_falls_back_to_the_draft(monkeypatch):
    _allow_polish(monkeypatch)

    async def boom(**_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ai_readme, "_resolve_system_model", boom)
    assert await polish_readme(_DRAFT, pod_name="Trumpet", description=None, **_IDS) is None
