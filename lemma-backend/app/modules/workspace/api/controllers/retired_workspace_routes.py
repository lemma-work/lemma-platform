"""The `/workspace/*` routes machines replaced, each saying where it went.

A person's workspace became their default machine, `me`, and its routes moved
under `/machines/me/...`. Anything still calling the old paths -- an older
CLI or SDK, a pod app built against them, a bookmarked script -- would
otherwise get a bare 404, which reads as "this never existed" rather than "it
moved". Each answers 410 Gone with the replacement in the body, and the
browser view's socket closes with :data:`RETIRED_SOCKET_CLOSE_CODE` and the
new path as its reason.

Out of the public OpenAPI, so no SDK grows a method for a route that only
refuses. Removable once no client of the old paths can still be in use.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket

from app.core.domain.errors import DomainError

router = APIRouter(tags=["Machines"], include_in_schema=False)

#: An application close code (4000-4999): 4000 + the HTTP status it stands for.
RETIRED_SOCKET_CLOSE_CODE = 4410
RETIRED_CODE = "WORKSPACE_ROUTE_RETIRED"


@dataclass(frozen=True, slots=True)
class Retired:
    method: str
    path: str
    replacement: str


#: Every route `/machines` replaced, and its replacement on the default machine.
RETIRED_ROUTES: tuple[Retired, ...] = (
    Retired("GET", "/workspace/status", "GET /machines/me"),
    Retired("GET", "/workspace/files", "GET /machines/me/files"),
    Retired("GET", "/workspace/files:stat", "GET /machines/me/files:stat"),
    Retired("GET", "/workspace/files:content", "GET /machines/me/files:content"),
    Retired("GET", "/workspace/browser/status", "GET /machines/me/browser"),
    Retired(
        "GET",
        "/workspace/browser/current-page-url",
        "GET /machines/me/browser/current-page-url",
    ),
    Retired(
        "POST",
        "/workspace/browser/display-size",
        "POST /machines/me/browser:display",
    ),
    Retired(
        "POST",
        "/workspace/apps/browser/access",
        "POST /machines/me/browser:access",
    ),
)

RETIRED_BROWSER_VIEW = Retired(
    "WEBSOCKET", "/workspace/browser/view", "/machines/me/browser/view"
)


class WorkspaceRouteRetired(DomainError):
    def __init__(self, retired: Retired) -> None:
        super().__init__(
            f"{retired.method} {retired.path} has been retired. Use "
            f"{retired.replacement} instead.",
            code=RETIRED_CODE,
            status_code=410,
            details={"replacement": retired.replacement},
        )


def _refuse(retired: Retired) -> Callable[[], Awaitable[None]]:
    async def refuse() -> None:
        raise WorkspaceRouteRetired(retired)

    refuse.__name__ = "retired_" + retired.path.strip("/").replace("/", "_").replace(
        ":", "_"
    ).replace("-", "_")
    return refuse


for _retired in RETIRED_ROUTES:
    router.add_api_route(_retired.path, _refuse(_retired), methods=[_retired.method])


@router.websocket(RETIRED_BROWSER_VIEW.path)
async def retired_browser_view(websocket: WebSocket) -> None:
    """Accepted only to be closed with a reason a client can show: a socket
    refused before it is accepted reaches the browser as a bare 403."""
    await websocket.accept()
    await websocket.close(
        code=RETIRED_SOCKET_CLOSE_CODE,
        reason=f"Moved to {RETIRED_BROWSER_VIEW.replacement}",
    )


__all__ = [
    "RETIRED_BROWSER_VIEW",
    "RETIRED_CODE",
    "RETIRED_ROUTES",
    "RETIRED_SOCKET_CLOSE_CODE",
    "router",
]
