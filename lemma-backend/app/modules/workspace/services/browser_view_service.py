"""Turning "this person wants to see their browser" into a live connection.

The whole of the fabric-specific work lives below this: ensure the sandbox is
up, hand its relay the token, find the browser (starting it and steering it to
an origin if asked), and produce the socket URL to attach to.

Ownership is not checked here, because there is nothing to check. A workspace
sandbox is keyed by user id, so resolving *this person's* sandbox is what makes
the view theirs -- there is no identifier a caller could pass to reach somebody
else's. That is worth stating, because the previous design took a sandbox id
from the request and had to defend it.
"""

from __future__ import annotations

from contextlib import suppress
from uuid import UUID


import httpx

from app.core.log.log import get_logger
from app.modules.workspace.domain.sandbox import SandboxKind, SandboxOwnerKind
from app.modules.workspace.services.browser_relay_client import (
    BrowserRelayClient,
    BrowserRelayUnavailable,
)
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)
from typing import TypedDict

from app.modules.workspace.contracts.browser import BrowserState, host_of
from app.modules.workspace.providers.base import ProviderGone
from sandbox_runtime.errors import SandboxCapabilityUnsupported

logger = get_logger(__name__)


def _engine_error() -> type[Exception]:
    """Docker's own failure type, named when it is caught rather than at import.

    Naming it at module scope pulls the engine client and the runtime client
    into the API's import graph for every process that merely registers these
    routes, and this is an `except` clause.
    """
    from app.modules.workspace.providers.docker_engine import DockerEngineError

    return DockerEngineError


class BrowserStatus(TypedDict, total=False):
    """What a pane is told, without anything being started to find out."""

    state: str
    detail: str


#: What a viewer may ask to do. Watching is the default everywhere; driving is
#: only ever offered to the person whose sandbox it is.
MODE_VIEW = "view"
MODE_CONTROL = "control"


class BrowserViewService:
    """One person's browser, reachable from the API."""

    def __init__(self, workspace: WorkspaceSandboxService | None = None) -> None:
        self._workspace = workspace or WorkspaceSandboxService()

    async def close(self) -> None:
        await self._workspace.close()

    async def keep_awake(self, user_id: UUID) -> None:
        """Record that this person is still using their sandbox.

        Called on a timer for as long as a view socket is open. Watching is not
        a tool call, and the idle sweep measures from the last time somebody
        asked for the sandbox -- so a person reading a page, or typing a
        password slowly, looked idle the whole time and had their computer
        stopped underneath them after `idle_release_seconds`. Releasing runs
        quiesce, which deletes the browser profile, so what they lost was the
        sign-in they were in the middle of.

        Best effort: this keeps something alive, and failing to do so must not
        take down the socket that was working.
        """
        from app.modules.workspace.services.sandbox_composition import (
            get_sandbox_service,
        )

        with suppress(Exception):
            service = get_sandbox_service()
            sandbox = await service.resolve(
                kind=SandboxKind.WORKSPACE,
                owner_kind=SandboxOwnerKind.USER,
                owner_id=user_id,
            )
            await service.touch(sandbox.id)

    async def _relay(self, user_id: UUID, *, start: bool) -> BrowserRelayClient:
        """The relay for this person's sandbox, with its token delivered.

        `start` is the difference between a pane rendering and a person
        arriving: rendering a status must not wake a paused sandbox, and
        somebody who clicked a link has asked for exactly that.
        """
        from app.modules.workspace.services.sandbox_composition import (
            get_sandbox_service,
        )

        service = get_sandbox_service()
        sandbox = await service.resolve(
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=user_id,
        )

        if not start:
            # Never provisions: a pane asking what to render must not be what
            # starts a container.
            info = await service.describe(sandbox.id)
            if info is None or info.status != "RUNNING":
                raise BrowserRelayUnavailable("this computer is not running")

        handle = await service.ensure(sandbox.id)
        provider, instance = service.reach(handle)
        relay = BrowserRelayClient(provider, instance)
        await relay.deliver_token()
        return relay

    async def status(self, user_id: UUID) -> BrowserStatus:
        """What a pane can say without waking anything.

        Every failure here is a sentence rather than an exception, because this
        is what renders in a panel: "asleep" and "this kind of computer cannot
        do that" are answers, not errors.
        """
        try:
            relay = await self._relay(user_id, start=False)
        except SandboxCapabilityUnsupported as exc:
            return {"state": "unsupported", "detail": str(exc)}
        except BrowserRelayUnavailable:
            return {"state": "asleep"}
        except (OSError, httpx.HTTPError, ProviderGone, _engine_error()) as exc:
            # A provider that cannot be reached, or a container that went away
            # between the two calls. This renders in a panel, so it answers with
            # a state rather than a traceback -- but only for failures meaning
            # "not right now". Anything else is a bug and must surface as one.
            logger.warning(
                "workspace.browser_view.status_failed.degraded",
                error_type=type(exc).__name__,
            )
            return {"state": "unavailable"}

        try:
            chrome = await relay.health(start=True)
        except BrowserRelayUnavailable:
            # The relay is not answering. On a sandbox that predates it that is
            # permanent until the image is replaced, which is a different
            # remedy from "wake it", so it gets its own state.
            logger.warning("workspace.browser_view.relay_absent.degraded")
            return {"state": "unavailable"}

        return {"state": "running" if chrome == "running" else "stopped"}

    async def open_session(
        self,
        user_id: UUID,
        *,
        mode: str,
        origin: str | None = None,
        session: str | None = None,
        domain: str | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Get a browser up, on the right page, and say where to attach.

        Raises `BrowserRelayUnavailable` with a sentence when the browser will
        not start, and `SandboxCapabilityUnsupported` where this fabric cannot
        reach a port at all.
        """
        relay = await self._relay(user_id, start=True)
        # The same rule the state paths hold, on the path a person actually
        # uses. `load_login_state` and `ensure_for_sign_in` both refused a
        # sandbox the internet can reach; this one -- the socket somebody types
        # a password into -- did not, so the guard was on the two doors nobody
        # was walking through.
        await _require_private(relay, doing="watch or drive this browser")
        # No session named and a site named means a sign-in: it belongs in that
        # site's own session, the one `save_login_state` later reads. The relay
        # names that session from `domain` and not from `origin`, so an origin
        # on its own used to land in the default session -- the whole of the
        # bug this pairing removes. `ensure_for_sign_in` already did this; the
        # viewer did not, and they are the two halves of one journey.
        if session is None and domain is None and origin:
            domain = host_of(origin)
        found = await relay.ensure_browser(
            origin=origin, session=session, domain=domain
        )
        target_id = str(found.get("target_id") or "")
        if not target_id:
            raise BrowserRelayUnavailable("the browser has no page to show")
        # The session the relay says it used, never one worked out again here.
        #
        # This used to re-derive `login-<host>` from the origin while the relay,
        # given neither a session nor a domain, had opened the page in the
        # default one. The socket then carried a target id from one browser to
        # another, where it does not exist -- so the person watched a reconnect
        # loop, and a capture afterwards read a browser nobody had signed in to.
        # Two derivations of one fact is the bug; this is the one that knows.
        attached = str(found.get("session") or "") or None
        return await relay.session_socket_url(
            target_id=target_id, mode=mode, session=attached
        )

    async def save_login_state(
        self, user_id: UUID, *, domain: str, session: str | None = None
    ) -> "BrowserState":
        relay = await self._relay(user_id, start=True)
        return await relay.save_state(domain=domain, session=session)

    async def load_login_state(
        self,
        user_id: UUID,
        state: "BrowserState | dict[str, object]",
        *,
        domain: str,
        session: str | None = None,
    ) -> None:
        relay = await self._relay(user_id, start=True)
        await _require_private(relay, doing="load a saved login")
        await relay.load_state(state, domain=domain, session=session)

    async def ensure_for_sign_in(
        self,
        user_id: UUID,
        *,
        origin: str,
        report: bool = False,
        session: str | None = None,
    ) -> dict[str, object] | None:
        """Put the site in front of the person before they arrive.

        Called when a sign-in is asked for, not when the person opens the link:
        by the time they arrive the browser may have retired for idleness, so
        this is a best effort that the arrival repeats. What it buys is the
        common case where they click straight away.

        `report` returns where the browser landed -- address and page title --
        for the one caller that needs to know whether the site accepted a
        restored session or bounced it to a login form. Off by default because
        the other callers are opening a page for a person, not asking a
        question about it.
        """
        relay = await self._relay(user_id, start=True)
        await _require_private(relay, doing="sign in to a site")
        # In the site's own session, which is the session `save_login_state`
        # reads. Opening it in the default one and capturing from the login one
        # means capturing from a browser nobody ever signed in to.
        #
        # `session` overrides that for the one caller that is not opening a
        # page for a person: checking whether a restored session still works
        # has to look at the browser the *agent* will use, or it answers a
        # question nobody asked.
        landed = await relay.ensure_browser(
            origin=origin,
            session=session,
            domain=None if session else host_of(origin),
        )
        return landed if report else None


async def _require_private(relay, *, doing: str) -> None:
    """Refuse to put anybody's session into a sandbox the internet can reach.

    On E2B every published port is a public name. New sandboxes are created
    with public traffic disabled and answer 403 without a per-sandbox token,
    but that flag is set at create and cannot be changed afterwards -- so a
    sandbox made before it existed stays open for its whole life. `reach_port`
    already reports which kind it is; until now nothing asked.

    It matters here more than anywhere else because of what is also in that
    sandbox: the agent-browser dashboard, republished on `0.0.0.0:4848` with
    nothing in front of it. It is not the passive viewer it was once described
    as -- it has a Storage panel that lists the browser's cookies and a console
    that evaluates script. Loading somebody's saved session into a browser
    behind that is handing their account to whoever finds the address.

    So the two paths that put a session into a browser refuse. Watching is not
    refused: a viewer reaches the browser through this API over an
    authenticated socket, and nothing about the sandbox's own address changes
    what that person is already entitled to see.
    """
    try:
        public = await relay.endpoint_is_public()
    except OSError, httpx.HTTPError, ProviderGone, _engine_error():
        # Cannot tell. Refusing on a failed probe would lock people out of a
        # working sandbox; this is the one place the safe answer is the
        # permissive one, because the *other* paths to this browser -- the ones
        # that could leak -- are gated by the same check when they run.
        return
    if not public:
        return
    logger.warning("workspace.browser_view.public_sandbox_refused.denied")
    raise BrowserRelayUnavailable(
        f"this computer's ports are reachable from the internet, so it will "
        f"not be used to {doing}. Restart it and try again -- a replacement is "
        f"created closed."
    )


__all__ = ["MODE_CONTROL", "MODE_VIEW", "BrowserViewService"]
