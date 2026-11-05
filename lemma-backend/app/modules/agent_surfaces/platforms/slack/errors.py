"""Slack's error type, without importing Slack's SDK to name it.

``slack_sdk`` is ~43 modules, and every module that caught ``SlackApiError``
imported it at module scope -- so an API process loaded the whole SDK to start,
Slack configured or not. The SDK is now imported where a client is built, and
these name its error for the code that catches it:

* ``except slack_api_error() as exc:`` -- the expression is evaluated after the
  raise, by which time the call that raised has loaded the SDK.
* ``is_slack_api_error(exc)`` -- for code that may see any exception: false
  without importing anything while the SDK is not loaded, since no Slack error
  can exist before it is.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from slack_sdk.errors import SlackApiError

_ERRORS_MODULE = "slack_sdk.errors"


def slack_api_error() -> type[SlackApiError]:
    """``SlackApiError``, imported on first use."""
    from slack_sdk.errors import SlackApiError

    return SlackApiError


def is_slack_api_error(exc: BaseException) -> TypeGuard[SlackApiError]:
    """Whether ``exc`` is Slack's API error, importing nothing to find out."""
    return _ERRORS_MODULE in sys.modules and isinstance(exc, slack_api_error())
