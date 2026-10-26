"""Every unit test here reads and writes surface rows in memory."""

from __future__ import annotations

import pytest

from app.modules.test_support.conversation_surfaces_double import SurfaceRows


@pytest.fixture(autouse=True)
def surface_rows(monkeypatch: pytest.MonkeyPatch) -> SurfaceRows:
    """``surface_conversation_state``, as a dict the test can read and seed."""
    return SurfaceRows().install(monkeypatch)
