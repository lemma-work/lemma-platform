"""Who opened a machine socket, and whether they may use the machine it names.

A browser cannot set headers on a WebSocket handshake, so these sockets
authenticate themselves, in the same order as the browser view: the page's
origin first -- browsers send cookies to WebSockets regardless of origin, so
without it any page could open a terminal as the signed-in person -- then the
session from a bearer, the cookie, or ``access_token``.

Every refusal accepts the socket before closing it, so the close code reaches
page script: a close before ``accept()`` is a rejected handshake, which a
browser reports only as 1006.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import WebSocket

from app.core.config import settings
from app.core.cors import get_allowed_cors_origin_regex
from app.core.log.log import get_logger
from app.modules.workspace.services.ws_bridge import origins_from
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    MachineCaller,
)
from app.modules.workspace.domain.machine_errors import (
    MachineAccessDenied,
    MachineConsentRequired,
    MachineNotFound,
)
from app.modules.workspace.services.machine_resolver import (
    MachineResolver,
    ResolvedMachine,
)

logger = get_logger(__name__)

CLOSE_UNAUTHENTICATED = 4401
CLOSE_ORIGIN_REFUSED = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_CONSENT_REQUIRED = 4428


async def _hang_up(websocket: WebSocket, code: int) -> None:
    try:
        await websocket.accept()
        await websocket.close(code=code)
    except (RuntimeError, AttributeError, OSError) as exc:
        logger.debug(
            "workspace.machine_socket.close_not_delivered.observed",
            close_code=code,
            error_type=type(exc).__name__,
        )


async def authorize_socket(
    websocket: WebSocket,
    ref: str,
    *,
    need: MachineAccess = MachineAccess.USE,
) -> ResolvedMachine | None:
    """The machine, or None after closing the socket with the reason."""
    from app.core.api.dependencies import get_uow_factory
    from app.modules.workspace.api.controllers.browser_view_session import (
        resolve_user_id,
        session_user_id,
    )
    from app.modules.workspace.services.ws_bridge import origin_is_allowed

    sent = websocket.headers.get("origin")
    if sent is not None and not origin_is_allowed(
        sent, allowed=allowed_origins(), pattern=allowed_origin_pattern()
    ):
        await _hang_up(websocket, CLOSE_ORIGIN_REFUSED)
        return None
    session_user = await session_user_id(
        websocket,
        resolve_user_id,
        socket="machine",
    )
    if session_user is None:
        await _hang_up(websocket, CLOSE_UNAUTHENTICATED)
        return None
    user_id = UUID(session_user)
    try:
        async with get_uow_factory()() as uow:
            caller = await _caller(websocket, user_id)
            resolved = await MachineResolver(uow).resolve(ref, caller, need=need)
            await uow.commit()
    except MachineConsentRequired:
        await _hang_up(websocket, CLOSE_CONSENT_REQUIRED)
        return None
    except MachineNotFound, MachineAccessDenied:
        await _hang_up(websocket, CLOSE_NOT_FOUND)
        return None
    return resolved


async def _caller(websocket: WebSocket, user_id: UUID) -> MachineCaller:
    """A socket's caller: the person, or -- from an app's page -- the app,
    recognised the way ``core/app_pin`` recognises a request."""
    from app.core.app_pin import UnknownAppHost, app_of_connection

    try:
        app = await app_of_connection(websocket)
    except UnknownAppHost:
        # An app host naming no app keeps nothing of its viewer's.
        return MachineCaller(CallerKind.APP, user_id=user_id)
    if app is None:
        return MachineCaller(CallerKind.USER, user_id=user_id)
    return MachineCaller(
        CallerKind.APP,
        user_id=user_id,
        pod_id=app.pod_id,
        app_id=app.app_id,
        app_name=app.name,
    )


def allowed_origins() -> tuple[str, ...]:
    """Where a browser may legitimately open this socket from.

    **`cors_origins` is the list, and that is the fix.** This used to name
    three settings of its own -- `frontend_url`, `api_url`,
    `auth_frontend_url` -- and a deployment that serves the app on more than
    one hostname then had a browser-view allowlist narrower than the
    allowlist every other request in the process is checked against. Watched
    in production: the app was served on an apex and a `www.` host, both in
    `cors_origins`, while this permitted only the `frontend_url` host. A
    person handed the wheel for a sign-in had their socket refused six times
    and never saw a screen, and the agent reported that they had chosen not
    to sign in.

    The three settings stay in, because one of them can legitimately be
    absent from `cors_origins` on a single-host deployment, and dropping a
    door somebody is already coming through is not a fix. They go through
    `_origin_of` now, so a configured URL that carries a path contributes
    the origin it is on rather than a string no browser can match.

    A dependency rather than a module function so a test can supply its own,
    which is injection rather than reaching into the module under test and
    replacing part of it.
    """
    return origins_from(
        settings.cors_origins,
        settings.frontend_url,
        settings.api_url,
        getattr(settings, "auth_frontend_url", None),
    )


def allowed_origin_pattern() -> str | None:
    """The pattern HTTP CORS uses, for deployments whose frontends are
    per-tenant.

    `get_allowed_cors_origin_regex`, not `settings.cors_origin_regex`: the
    helper combines the configured pattern with the generated app-subdomain
    one, and taking only the configured half would recreate this whole
    change one layer down -- HTTP accepting an origin while the browser-view
    socket refuses it with 4403. Two allowlists that disagree is the fault
    being fixed here, not a shape to repeat.

    Kept a separate dependency so a test can supply one without also
    supplying the list.
    """
    return get_allowed_cors_origin_regex()


async def machine_for_view(
    websocket: WebSocket,
    ref: str,
    user_id: UUID,
    *,
    need: MachineAccess,
    refuse: Callable[[WebSocket, int], Awaitable[None]],
):
    """The machine this socket names, or None after refusing it.

    Driving needs use of the machine; watching, only sight of it -- a pod
    viewer may watch a pod machine's agent work and may not take the wheel.
    """
    from app.core.api.dependencies import get_uow_factory

    try:
        async with get_uow_factory()() as uow:
            caller = await _caller(websocket, user_id)
            resolved = await MachineResolver(uow).resolve(ref, caller, need=need)
            await uow.commit()
    except MachineConsentRequired:
        await refuse(websocket, CLOSE_CONSENT_REQUIRED)
        return None
    except MachineNotFound, MachineAccessDenied:
        await refuse(websocket, CLOSE_NOT_FOUND)
        return None
    return resolved


ViewMachineResolver = Callable[..., Awaitable["ResolvedMachine | None"]]


def view_machine_resolver() -> ViewMachineResolver:
    """The lookup the browser view socket uses, as something a test supplies --
    the same shape as `user_id_resolver`."""
    return machine_for_view
