"""The refusal that has to be tellable apart from every other refusal.

Provisioning logs `failure_code`, never the exception text -- the log pipeline
strips any `error` field because exception messages carry keys and personal
data. That only works if the code names the branch that refused, and
`AGENT_SURFACE_VALIDATION_ERROR` did not: a pod silently getting no mailbox
looked identical to a bad name or a mode mismatch.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceRuntimeUnsupportedError,
    AgentSurfaceValidationError,
)

pytestmark = pytest.mark.unit


def test_the_runtime_refusal_has_its_own_code() -> None:
    error = AgentSurfaceRuntimeUnsupportedError("no public URL")
    assert error.code == "AGENT_SURFACE_RUNTIME_UNSUPPORTED"
    assert error.code != AgentSurfaceValidationError("x").code


def test_it_is_still_a_validation_error() -> None:
    """So every existing handler and the 422 are unchanged."""
    error = AgentSurfaceRuntimeUnsupportedError("no public URL")
    assert isinstance(error, AgentSurfaceValidationError)
    assert error.status_code == 422


def test_a_local_runtime_without_polling_is_refused_and_says_which_setting(
    monkeypatch,
) -> None:
    """The message names the setting, because "requires a public HTTPS URL" left
    the reader to discover three env vars by reading the code."""
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.services import surface_service as module

    monkeypatch.setattr(module, "public_https_api_url_available", lambda: False)
    monkeypatch.setattr(surface_settings, "enable_resend_polling_mode", False)

    service = module.AgentSurfaceService.__new__(module.AgentSurfaceService)
    surface = AgentSurfaceEntity(
        pod_id=__import__("uuid").uuid4(),
        agent_id=__import__("uuid").uuid4(),
        name="email",
        surface_type=SurfacePlatform.RESEND,
        config=SurfaceConfig(),
    )
    with pytest.raises(AgentSurfaceRuntimeUnsupportedError) as caught:
        service._validate_runtime_supported(surface)
    assert "ENABLE_RESEND_POLLING_MODE" in str(caught.value)


def test_polling_mode_makes_a_local_email_surface_supported(monkeypatch) -> None:
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.services import surface_service as module

    monkeypatch.setattr(module, "public_https_api_url_available", lambda: False)
    monkeypatch.setattr(surface_settings, "enable_resend_polling_mode", True)

    service = module.AgentSurfaceService.__new__(module.AgentSurfaceService)
    surface = AgentSurfaceEntity(
        pod_id=__import__("uuid").uuid4(),
        agent_id=__import__("uuid").uuid4(),
        name="email",
        surface_type=SurfacePlatform.RESEND,
        config=SurfaceConfig(),
    )
    service._validate_runtime_supported(surface)  # does not raise
