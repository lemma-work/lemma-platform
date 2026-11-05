"""Which surface a new conversation is counted under."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.conversation_metadata import surface_of


@pytest.mark.parametrize(
    "metadata, surface",
    [
        (None, "app"),
        ({}, "app"),
        ({"source": "web_widget"}, "web_widget"),
        ({"source": "agent_surfaces", "surface_platform": "SLACK"}, "SLACK"),
        ({"source": "agent_surfaces"}, "other"),
        ({"source": "notification"}, "notification"),
        ({"workflow_run_id": str(uuid4())}, "workflow"),
    ],
)
def test_surface_of(metadata, surface) -> None:
    assert surface_of(metadata) == surface
