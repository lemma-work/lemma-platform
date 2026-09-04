"""What the vision delegate says, and what it costs, when it cannot look.

Vision is a tool, so its failures have to arrive as tool failures. The delegate
reserves spend of its own before it calls the model, and the two callers in
`tools/vision_delegation.py` catch only `VisionUnavailableError` and
`VisionDescriptionError` -- so a raw `UsageLimitExceededError` escaping the
delegate ended the whole run over one image the agent could have done without.
"""

from __future__ import annotations

import pytest

from app.modules.agent.services.vision_service import (
    VisionDescriptionError,
    VisionUnavailableError,
    allowance_refusal,
)
from app.modules.agent.tools.vision_delegation import describe_single_image

pytestmark = pytest.mark.unit


def test_an_exhausted_allowance_is_a_vision_failure_the_tool_can_report():
    refusal = allowance_refusal()

    assert isinstance(refusal, VisionDescriptionError)
    # Not the unavailable type: nothing is misconfigured, and telling somebody
    # to set VISION_MODEL when the real answer is "the month ran out" sends
    # them to the wrong place.
    assert not isinstance(refusal, VisionUnavailableError)


def test_the_refusal_says_it_is_spend_and_says_what_ends_it():
    text = str(allowance_refusal())

    # The agent relays this to a person, so it has to carry both halves: why it
    # stopped, and what makes it start working again.
    assert "allowance" in text
    assert "resets" in text and "plan limit" in text


async def test_the_tool_reports_the_refusal_instead_of_failing_the_run():
    """The whole point: `view_image` comes back unsuccessful, not exploded."""

    async def out_of_allowance(*args, **kwargs) -> str:
        raise allowance_refusal()

    response = await describe_single_image(
        _context(),
        data=b"not really a png",
        media_type="image/png",
        file_path="chart.png",
        source="workspace",
        instructions=None,
        describe=out_of_allowance,
    )

    assert response.success is False
    assert "allowance" in (response.error or "")


def _context():
    from types import SimpleNamespace
    from uuid import uuid4

    return SimpleNamespace(user_id=uuid4(), organization_id=uuid4())
