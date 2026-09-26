"""What "no model is set up" says, and what the auxiliary paths do about it.

The error text is read by whoever can fix it: an operator with an environment
on a server, the person at the keyboard on Desktop. The code is what the web
app keys its "set up a model" link off, so it must not move.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.modules.agent.config import agent_settings
from app.modules.agent.services import summarization_model, vision_service
from app.modules.agent.services.runtime_profile_service import (
    AgentRuntimeProfileService,
)

pytestmark = pytest.mark.unit


def _no_system_model(monkeypatch: pytest.MonkeyPatch, *, kind: str) -> None:
    for name in (
        "LEMMA_OPENAI_API_KEY",
        "LEMMA_ANTHROPIC_API_KEY",
        "LEMMA_DEFAULT_MODEL_TYPE",
        "VISION_MODEL",
        "HISTORY_SUMMARIZATION_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "app.modules.identity.config.identity_settings.deployment_kind", kind
    )
    # Server mode still reads a checkout's `.env`; keep a developer's keys out.
    monkeypatch.setattr(
        "app.modules.agent.services.runtime_system_profiles.dotenv_values",
        lambda _path: {},
    )
    monkeypatch.setattr(settings, "lemma_openai_api_key", None)
    monkeypatch.setattr(agent_settings, "lemma_anthropic_api_key", None)
    monkeypatch.setattr(agent_settings, "lemma_default_model_type", "openai_compat")
    monkeypatch.setattr(agent_settings, "vision_model", None)
    monkeypatch.setattr(agent_settings, "history_summarization_model", None)


async def _resolve_default() -> DomainError:
    with pytest.raises(DomainError) as caught:
        await AgentRuntimeProfileService().resolve(
            runtime=None, organization_id=None, user_id=uuid4()
        )
    return caught.value


@pytest.mark.asyncio
async def test_desktop_points_at_the_apps_own_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_system_model(monkeypatch, kind="desktop")

    error = await _resolve_default()

    assert error.code == "model_not_configured"
    assert error.status_code == 503
    assert error.message == (
        "No AI model is set up yet. Set up an AI model in This Mac → Server "
        "setup, or add a provider in Settings → Models."
    )
    assert "LEMMA_" not in error.message


@pytest.mark.asyncio
async def test_a_server_names_the_page_not_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader may be any member; environment variables go to the log."""
    _no_system_model(monkeypatch, kind="server")

    error = await _resolve_default()

    assert error.code == "model_not_configured"
    assert error.message == (
        "No AI model is set up yet. Add a provider in Settings \u2192 Models."
    )
    assert "LEMMA_" not in error.message


def test_without_a_system_model_the_delegate_may_come_from_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the database knows whether a workspace model reads images, and this
    check is synchronous -- so it says yes and the tool reports if none does."""
    _no_system_model(monkeypatch, kind="desktop")

    assert vision_service.vision_delegate_available() is True


@pytest.mark.asyncio
async def test_summaries_stay_on_the_runs_model_without_a_system_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The setting names a system-provider model; without that provider the
    run's own model -- the workspace's -- is the documented fallback."""
    _no_system_model(monkeypatch, kind="desktop")
    monkeypatch.setenv("HISTORY_SUMMARIZATION_MODEL", "small-model")
    fallback = object()

    model = await summarization_model.resolve_summarization_model(
        organization_id=uuid4(), user_id=uuid4(), fallback=fallback
    )

    assert model is fallback


@pytest.mark.asyncio
async def test_vision_without_an_organization_is_unavailable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_system_model(monkeypatch, kind="desktop")

    with pytest.raises(vision_service.VisionUnavailableError) as caught:
        await vision_service._resolve_vision_model(
            organization_id=None, user_id=uuid4()
        )

    assert "reads images" in str(caught.value)
