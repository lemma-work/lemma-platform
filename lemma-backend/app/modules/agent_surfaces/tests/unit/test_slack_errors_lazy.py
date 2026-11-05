"""Slack's errors join the transport family once Slack's SDK is loaded, not before.

In a fresh interpreter, because by the time any test runs something has already
imported ``slack_sdk``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[5]

_PROBE = """
import sys
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.slack.errors import is_slack_api_error

assert "slack_sdk" not in sys.modules, "naming the family imported slack_sdk"
assert not is_slack_api_error(ValueError("x"))
assert "slack_sdk" not in sys.modules, "asking about an error imported slack_sdk"
assert all(e.__module__.split(".")[0] != "slack_sdk" for e in platform_transport_errors())

from slack_sdk.errors import SlackApiError, SlackClientError

family = platform_transport_errors()
assert SlackApiError in family and SlackClientError in family
error = SlackApiError("rate limited", {"ok": False, "error": "ratelimited"})
assert is_slack_api_error(error)
try:
    raise error
except platform_transport_errors():
    pass
print("ok")
"""


def test_slack_errors_are_matched_once_slack_is_loaded() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip().splitlines()[-1] == "ok"
