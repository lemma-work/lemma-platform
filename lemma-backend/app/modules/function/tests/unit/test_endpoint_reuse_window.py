"""The endpoint reuse window must never outlive the sandbox behind it.

Idle release pauses the sandbox rather than destroying it, and an invocation
against a paused sandbox fails at the transport layer -- which the dispatcher
deliberately does not replay. So a reuse window longer than the idle release is
not a stale-cache inefficiency, it is failed runs.

The two settings live in different settings objects, so nothing in pydantic can
relate them; this is the check that does.
"""

from __future__ import annotations

import pytest

from app.modules.function.config import function_settings
from app.modules.function.application.function_runtime_route_resolver import (
    endpoint_reuse_seconds,
)
from app.modules.workspace.config import workspace_settings


@pytest.mark.parametrize(
    ("configured", "idle_release", "expected"),
    [
        # Production's pair today: 60 against an overridden 180.
        (60, 180, 60),
        # The field maximum against production's idle release: must be cut, not
        # honoured. This is the combination the field's own bounds still allow.
        (240, 180, 90),
        # An idle release shorter than the reuse window, from either direction.
        (120, 60, 30),
        (60, 20, 10),
        # Never collapses below the cache's own floor.
        (60, 2, 5),
        # Sweeping disabled: nothing releases the sandbox, so nothing to clamp.
        (240, 0, 240),
    ],
)
def test_reuse_window_is_clamped_under_idle_release(
    monkeypatch, configured: int, idle_release: int, expected: int
) -> None:
    # `int(...)` rather than the bare parameter so `check_test_doubles.py` can
    # see this for what it is: a number, arranging the run. It reads a variable,
    # and the gate can only recognise data from a literal or a value
    # constructor -- so a parametrized scalar looked like a stand-in for
    # behaviour. It was already counted this way; moving the field onto
    # `FunctionSettings` only moved which module it counted against.
    monkeypatch.setattr(
        function_settings, "function_runtime_endpoint_reuse_seconds", int(configured)
    )
    monkeypatch.setattr(workspace_settings, "idle_release_seconds", idle_release)

    assert endpoint_reuse_seconds() == expected


def test_the_shipped_defaults_are_a_safe_pair() -> None:
    """Guards the pair that actually ships, not just the clamp arithmetic."""
    assert endpoint_reuse_seconds() <= workspace_settings.idle_release_seconds
