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
from app.modules.workspace.providers.docker_engine import DockerEngineError
from sandbox_runtime.errors import SandboxCapabilityUnsupported

logger = get_logger(__name__)


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
        except (OSError, httpx.HTTPError, DockerEngineError, ProviderGone) as exc:
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
        found = await relay.ensure_browser(
            origin=origin, session=session, domain=domain
        )
        target_id = str(found.get("target_id") or "")
        if not target_id:
            raise BrowserRelayUnavailable("the browser has no page to show")
        # The socket attaches to the same session the page was opened in.
        # Attaching to the default one instead is how a person can sign in
        # perfectly and have the capture read an empty browser.
        attached = session or (
            _login_session(host_of(origin)) if domain or origin else None
        )
        return await relay.session_socket_url(
            target_id=target_id, mode=mode, session=attached
        )

    async def save_login_state(self, user_id: UUID, *, domain: str) -> "BrowserState":
        relay = await self._relay(user_id, start=True)
        return await relay.save_state(domain=domain)

    async def load_login_state(
        self, user_id: UUID, state: "BrowserState | dict[str, object]", *, domain: str
    ) -> None:
        relay = await self._relay(user_id, start=True)
        await relay.load_state(state, domain=domain)

    async def clear_login_state(self, user_id: UUID, *, domain: str) -> None:
        try:
            relay = await self._relay(user_id, start=False)
        except BrowserRelayUnavailable, SandboxCapabilityUnsupported:
            # Nothing to clear: no sandbox, or one this fabric cannot reach.
            return
        await relay.clear_state(domain=domain)

    async def ensure_for_sign_in(self, user_id: UUID, *, origin: str) -> None:  # noqa: D401
        """Put the site in front of the person before they arrive.

        Called when a sign-in is asked for, not when the person opens the link:
        by the time they arrive the browser may have retired for idleness, so
        this is a best effort that the arrival repeats. What it buys is the
        common case where they click straight away.
        """
        relay = await self._relay(user_id, start=True)
        # In the site's own session, which is the session `save_login_state`
        # reads. Opening it in the default one and capturing from the login one
        # means capturing from a browser nobody ever signed in to.
        await relay.ensure_browser(origin=origin, domain=host_of(origin))


def _login_session(domain: str) -> str:
    """Mirror of the relay's own naming, for addressing the same session."""
    from sandbox_runtime.browser_relay.state import session_for_domain

    return session_for_domain(domain)


__all__ = ["MODE_CONTROL", "MODE_VIEW", "BrowserViewService"]
