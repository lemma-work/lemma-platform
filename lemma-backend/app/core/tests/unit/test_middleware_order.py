"""Middleware order is part of the contract, not an implementation detail.

`add_middleware` prepends, so the last registration is the outermost layer. A
middleware that writes its own error response therefore has to be registered
*before* CORS to end up inside it -- otherwise the response it writes never
passes through `CORSMiddleware` and carries no `Access-Control-Allow-Origin`.

The one that got this wrong is the request-body limit. A browser upload over
`MAX_REQUEST_BODY_BYTES` was reported to the page as a CORS failure, with the
status and body withheld by the browser, so the carefully-worded
`UPLOAD_TOO_LARGE` envelope carrying `max_bytes` never reached anyone and the
frontend could not tell an oversized file from a misconfigured origin. The 429
path never had the problem: the abuse middleware is already inside CORS.
"""

from __future__ import annotations

import pytest

import app.app as appmod

pytestmark = pytest.mark.unit


def _layer_names(app) -> list[str]:
    """Outermost first, which is the reverse of registration order."""
    return [middleware.cls.__name__ for middleware in app.user_middleware]


def test_the_body_limit_sits_inside_cors_so_a_413_is_readable():
    layers = _layer_names(appmod.app)

    assert layers.index("CORSMiddleware") < layers.index("RequestBodyLimitMiddleware")


def test_the_correlation_id_stays_outermost():
    """Every response, including the ones written by middleware, is stamped."""
    assert _layer_names(appmod.app)[0] == "RequestObserverMiddleware"


def test_the_body_limit_still_wraps_the_body_buffering_abuse_middleware():
    """Order between these two is a memory bound, not a cosmetic choice.

    `AuthAbuseMiddleware` drains the request body to read the `email` field. The
    ceiling on how much it can be made to buffer is this middleware, so it has
    to stay outside it.
    """
    layers = _layer_names(appmod.app)

    assert layers.index("RequestBodyLimitMiddleware") < layers.index(
        "AuthAbuseMiddleware"
    )
