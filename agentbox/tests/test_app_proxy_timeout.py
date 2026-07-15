"""Manager app-proxy upstream-timeout resolution + timeout classification.

The proxy waits ``agentbox_app_proxy_timeout_seconds`` by default but honours a
per-request ``X-Agentbox-Upstream-Timeout`` override (clamped), so a long
synchronous function execute is not cut off mid-run. An upstream read timeout is
classified distinctly so the proxy can surface it as 504 (non-retryable) rather
than a retryable 502.
"""

from __future__ import annotations

import types
from urllib import error as urlerror

import pytest

from agentbox.api.apps import (
    UPSTREAM_TIMEOUT_HEADER,
    _is_timeout_error,
    resolve_upstream_timeout,
)
from agentbox.config import settings


def _request(headers: dict[str, str]):
    return types.SimpleNamespace(headers=headers)


def test_resolve_upstream_timeout_defaults_without_header():
    assert (
        resolve_upstream_timeout(_request({}))
        == settings.agentbox_app_proxy_timeout_seconds
    )


def test_resolve_upstream_timeout_uses_header():
    assert resolve_upstream_timeout(_request({UPSTREAM_TIMEOUT_HEADER: "300"})) == 300.0


def test_resolve_upstream_timeout_clamps_to_max():
    too_big = settings.agentbox_app_proxy_max_timeout_seconds + 1000
    assert (
        resolve_upstream_timeout(_request({UPSTREAM_TIMEOUT_HEADER: str(too_big)}))
        == settings.agentbox_app_proxy_max_timeout_seconds
    )


@pytest.mark.parametrize("value", ["abc", "0", "-5", ""])
def test_resolve_upstream_timeout_falls_back_on_invalid(value):
    assert (
        resolve_upstream_timeout(_request({UPSTREAM_TIMEOUT_HEADER: value}))
        == settings.agentbox_app_proxy_timeout_seconds
    )


def test_is_timeout_error_detects_timeouts():
    assert _is_timeout_error(TimeoutError())
    assert _is_timeout_error(urlerror.URLError(TimeoutError()))
    assert not _is_timeout_error(urlerror.URLError("connection refused"))
    assert not _is_timeout_error(OSError("boom"))
