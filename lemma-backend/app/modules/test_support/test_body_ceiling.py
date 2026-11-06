"""The envelope the route ceilings are compared against is the middleware's own.

Every route-ceiling test asserts its 413 equals ``too_large(max_bytes)``. That
is only worth something if ``too_large`` is what ``RequestBodyLimitMiddleware``
really sends, so a client that handles one 413 handles both.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.api.request_body import read_body_limited
from app.modules.test_support.body_ceiling import (
    GLOBAL_LIMIT_BYTES,
    app_with,
    deliver,
    too_large,
)

_router = APIRouter()


@_router.post("/limited")
async def _limited(request: Request) -> dict[str, int]:
    return {"read": len(await read_body_limited(request, max_bytes=8))}


async def test_a_route_ceiling_and_the_global_one_answer_in_one_envelope():
    app = app_with(_router)

    route = await deliver(app, "/limited", [b"x" * 9], declared_length=9)
    middleware = await deliver(
        app, "/limited", [b""], declared_length=GLOBAL_LIMIT_BYTES + 1
    )

    assert (route.status, route.json()) == (413, too_large(8))
    assert (middleware.status, middleware.json()) == (
        413,
        too_large(GLOBAL_LIMIT_BYTES),
    )


async def test_a_body_inside_the_ceiling_reaches_the_route():
    answered = await deliver(app_with(_router), "/limited", [b"x" * 8])

    assert (answered.status, answered.json()) == (200, {"read": 8})
