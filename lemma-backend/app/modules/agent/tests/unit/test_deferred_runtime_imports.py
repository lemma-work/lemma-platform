"""Config-gated code paths must survive their own deferred imports.

``vision_service._resolve_vision_model`` and
``summarization_model.resolve_summarization_model`` both import
``AgentRuntimeConfig`` inside the function body, to break an import cycle with
the harness. Both imported it from ``agent.domain.runtime_profiles``, which does
not define or re-export it -- it lives in ``app.core.domain.runtime``. Every
call therefore raised ``ImportError``.

Neither showed up, for the same reason in two shapes:

* ``VISION_MODEL`` and ``HISTORY_SUMMARIZATION_MODEL`` are unset by default, and
  both functions return early when unset -- so the import line was unreachable
  in every environment and every test.
* ``test_vision_modes`` patches ``describe_images``, so the delegation tests
  never descended into model resolution either.

The vision bug surfaced the first time an operator set ``VISION_MODEL``: the
agent reported the tool had failed. The summarization bug was quieter -- its
``except Exception`` logged at debug and fell back to the run's own model, so
setting ``HISTORY_SUMMARIZATION_MODEL`` to a cheap model silently kept using the
expensive one.

These tests drive both functions past the import with the setting set.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability

pytestmark = pytest.mark.unit


def _resolved_stub(capabilities):
    """What AgentRuntimeProfileService.resolve returns, minimally."""
    return SimpleNamespace(
        model=SimpleNamespace(capabilities=capabilities),
        credentials=None,
        public_snapshot=dict,
    )


class TestVisionModelResolution:
    @pytest.mark.asyncio
    async def test_resolving_a_vision_model_does_not_die_on_its_own_imports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.agent.services import vision_service

        monkeypatch.setenv("VISION_MODEL", "minimax-m3")
        sentinel = object()

        with (
            patch(
                "app.modules.agent.services.runtime_profile_service."
                "AgentRuntimeProfileService.resolve",
                new=AsyncMock(
                    return_value=_resolved_stub([RuntimeModelCapability.VISION])
                ),
            ),
            patch(
                "app.modules.agent.services.runtime_model_factory."
                "pydantic_ai_model_from_runtime_profile",
                return_value=sentinel,
            ),
        ):
            model, _profile = await vision_service._resolve_vision_model(
                organization_id=None, user_id=uuid4()
            )

        assert model is sentinel

    @pytest.mark.asyncio
    async def test_a_text_only_vision_model_is_rejected_by_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The capability guard must be reachable, not shadowed by ImportError.

        Its whole purpose is to fail with a message naming VISION_MODEL rather
        than as an opaque provider error -- which only works if execution gets
        that far.
        """
        from app.modules.agent.services import vision_service

        monkeypatch.setenv("VISION_MODEL", "deepseek-v4-flash")

        with patch(
            "app.modules.agent.services.runtime_profile_service."
            "AgentRuntimeProfileService.resolve",
            new=AsyncMock(return_value=_resolved_stub([RuntimeModelCapability.TEXT])),
        ):
            with pytest.raises(vision_service.VisionUnavailableError) as caught:
                await vision_service._resolve_vision_model(
                    organization_id=None, user_id=uuid4()
                )

        assert "VISION_MODEL" in str(caught.value)

    @pytest.mark.asyncio
    async def test_no_configured_model_raises_before_any_deferred_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The early-return path the module docstring describes but nothing
        actually exercised: with VISION_MODEL unset, resolution must fail fast
        naming the setting, without ever reaching the deferred imports (and so
        without ever calling out to AgentRuntimeProfileService)."""
        from app.modules.agent.services import vision_service

        monkeypatch.delenv("VISION_MODEL", raising=False)
        monkeypatch.setattr(vision_service.agent_settings, "vision_model", None)

        with patch(
            "app.modules.agent.services.runtime_profile_service."
            "AgentRuntimeProfileService.resolve",
            new=AsyncMock(side_effect=AssertionError("must not be called")),
        ):
            with pytest.raises(vision_service.VisionUnavailableError) as caught:
                await vision_service._resolve_vision_model(
                    organization_id=None, user_id=uuid4()
                )

        assert "VISION_MODEL" in str(caught.value)

    @pytest.mark.asyncio
    async def test_a_model_the_factory_cannot_build_is_reported_by_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`pydantic_ai_model_from_runtime_profile` returning None (an
        unrecognized/misconfigured protocol) must not surface as an
        ``AttributeError`` several layers down calling ``.request`` on
        ``None`` -- it is a vision-unavailable case like any other."""
        from app.modules.agent.services import vision_service

        monkeypatch.setenv("VISION_MODEL", "some-custom-model")

        with (
            patch(
                "app.modules.agent.services.runtime_profile_service."
                "AgentRuntimeProfileService.resolve",
                new=AsyncMock(
                    return_value=_resolved_stub([RuntimeModelCapability.VISION])
                ),
            ),
            patch(
                "app.modules.agent.services.runtime_model_factory."
                "pydantic_ai_model_from_runtime_profile",
                return_value=None,
            ),
        ):
            with pytest.raises(vision_service.VisionUnavailableError) as caught:
                await vision_service._resolve_vision_model(
                    organization_id=None, user_id=uuid4()
                )

        assert "some-custom-model" in str(caught.value)

    @pytest.mark.asyncio
    async def test_an_unresolved_catalog_entry_skips_the_capability_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No catalog entry (e.g. a legacy/unlisted model name) must not be
        treated as "missing VISION capability" -- there is nothing to check
        capabilities against, so resolution proceeds to build the model."""
        from app.modules.agent.services import vision_service

        monkeypatch.setenv("VISION_MODEL", "unlisted-model")
        sentinel = object()
        resolved_without_entry = SimpleNamespace(
            model=None, credentials=None, public_snapshot=dict
        )

        with (
            patch(
                "app.modules.agent.services.runtime_profile_service."
                "AgentRuntimeProfileService.resolve",
                new=AsyncMock(return_value=resolved_without_entry),
            ),
            patch(
                "app.modules.agent.services.runtime_model_factory."
                "pydantic_ai_model_from_runtime_profile",
                return_value=sentinel,
            ),
        ):
            model, _profile = await vision_service._resolve_vision_model(
                organization_id=None, user_id=uuid4()
            )

        assert model is sentinel


class TestConfiguredVisionModelName:
    def test_env_var_wins_over_the_settings_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.agent.services import vision_service

        monkeypatch.setattr(
            vision_service.agent_settings, "vision_model", "from-settings"
        )
        monkeypatch.setenv("VISION_MODEL", "from-env")

        assert vision_service.configured_vision_model_name() == "from-env"

    def test_falls_back_to_settings_when_env_var_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.agent.services import vision_service

        monkeypatch.delenv("VISION_MODEL", raising=False)
        monkeypatch.setattr(
            vision_service.agent_settings, "vision_model", "from-settings"
        )

        assert vision_service.configured_vision_model_name() == "from-settings"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_value_is_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch, blank: str
    ) -> None:
        from app.modules.agent.services import vision_service

        monkeypatch.setenv("VISION_MODEL", blank)

        assert vision_service.configured_vision_model_name() is None

    def test_nothing_configured_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.agent.services import vision_service

        monkeypatch.delenv("VISION_MODEL", raising=False)
        monkeypatch.setattr(vision_service.agent_settings, "vision_model", None)

        assert vision_service.configured_vision_model_name() is None
        assert vision_service.vision_delegate_available() is False

    def test_a_configured_name_makes_delegation_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.modules.agent.services import vision_service

        monkeypatch.setenv("VISION_MODEL", "some-model")

        assert vision_service.vision_delegate_available() is True


class TestSummarizationModelResolution:
    @pytest.mark.asyncio
    async def test_a_configured_summarization_model_is_actually_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returning the fallback is the bug's signature, not just an edge case.

        The point of the setting is to move ~70k-token compactions off the run's
        own (expensive) model. Silently returning `fallback` means the operator
        configured a cheap model and kept paying for the expensive one.
        """
        from app.modules.agent.services import summarization_model

        monkeypatch.setenv("HISTORY_SUMMARIZATION_MODEL", "deepseek-v4-flash")
        configured = object()
        fallback = object()

        with (
            patch(
                "app.modules.agent.services.runtime_profile_service."
                "AgentRuntimeProfileService.resolve",
                new=AsyncMock(
                    return_value=_resolved_stub([RuntimeModelCapability.TEXT])
                ),
            ),
            patch(
                "app.modules.agent.services.runtime_model_factory."
                "pydantic_ai_model_from_runtime_profile",
                return_value=configured,
            ),
        ):
            model = await summarization_model.resolve_summarization_model(
                organization_id=None, user_id=uuid4(), fallback=fallback
            )

        assert model is configured, "fell back to the run's own model"


def test_every_deferred_import_in_these_modules_resolves() -> None:
    """The class of bug, not just the two instances.

    Function-local imports exist here to break cycles with the harness, so they
    are invisible to module-import checks and to any test that does not reach
    the line. Resolving each one statically costs nothing and covers the next
    one somebody adds.
    """
    import importlib

    from app.modules.agent.services import summarization_model, vision_service

    failures: list[str] = []
    for module in (vision_service, summarization_model):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.ImportFrom) or node.module is None:
                    continue
                try:
                    imported = importlib.import_module(node.module)
                except Exception as exc:  # pragma: no cover - reported below
                    failures.append(f"{node.module}: {exc}")
                    continue
                for alias in node.names:
                    if not hasattr(imported, alias.name):
                        failures.append(
                            f"{module.__name__}.{function.name}: "
                            f"{node.module} has no {alias.name!r}"
                        )

    assert not failures, "unresolvable deferred imports:\n" + "\n".join(failures)
