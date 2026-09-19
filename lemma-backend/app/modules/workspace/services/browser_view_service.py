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
    ProfileCookies,
)
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)
from typing import TypedDict

from app.modules.workspace.contracts.browser import host_of
from app.modules.workspace.providers.base import ProviderGone
from sandbox_runtime.errors import SandboxCapabilityUnsupported, SandboxUnavailable

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
        stopped underneath them after `idle_release_seconds`.

        Less costly than it was: quiesce used to delete the whole browser
        profile, so a slow sign-in was thrown away rather than paused. It now
        removes only the lock files that name a dead process, and the profile
        survives. The sandbox still goes away mid-keystroke without this,
        which is reason enough.

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

    async def open_vnc_session(
        self,
        user_id: UUID,
        *,
        mode: str,
        origin: str | None = None,
        conversation_id: UUID | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Get a browser up, on the right page, and say where to attach a VNC view.

        Raises `BrowserRelayUnavailable` with a sentence when the browser will
        not start, and `SandboxCapabilityUnsupported` where this fabric cannot
        reach a port at all.

        `origin`, when given, steers the browser to that site first -- the
        "arrival repeats" self-heal that makes opening the sign-in page a
        second time land on the right site even if an earlier best-effort
        `ensure_for_sign_in` never ran or the browser had gone idle since.
        VNC shows the whole shared display rather than one CDP-picked tab, so
        there is no target to resolve the way the JSON stream this replaced
        needed -- but the session the steer actually landed in is still
        wanted, for the driving lease. See `vnc_socket_url`.

        `conversation_id` is carried for logging and for the keepalive, not
        to pick a browser: there is one per sandbox and everything shares it.
        It used to select `agent_session(conversation_id)`, a Chrome and
        profile of its own per conversation, which is what made a sign-in
        need carrying from one browser to another.

        `ensure_browser` is called either way, `origin` or not: it is what
        starts Xvfb, Chrome, and -- through `start-browser.sh` -- the VNC
        pair, none of which a mere port-forward through `deliver_token`
        brings up on its own. Skipping it for a plain watch/drive with no
        site to steer to was the first version of this method, and it left
        VNC connecting to a display nothing was running yet -- the browser
        used to start this way implicitly, through the JSON stream's own
        `ensure_browser` call, which VNC has no equivalent path for.
        """
        relay = await self._relay(user_id, start=True)
        # The same rule the state paths hold, on the path a person actually
        # uses. `forget_sites` and `ensure_for_sign_in` both refused a
        # sandbox the internet can reach; this one -- the socket somebody types
        # a password into -- did not, so the guard was on the two doors nobody
        # was walking through.
        await _require_private(relay, doing="watch or drive this browser")
        # No session is named, by any caller, ever. There is one browser in a
        # sandbox and it keeps its own profile, so a sign-in, an agent's
        # command and a person's pane are all looking at the same Chrome --
        # which is the point, and what removed the whole business of carrying
        # a captured login from one browser into another.
        found = await relay.ensure_browser(
            origin=origin,
            session=None,
            domain=host_of(origin) if origin else None,
        )
        # The session the relay says it used, never one worked out again
        # here -- see the note on `vnc_socket_url` for why this matters even
        # though VNC does not scope the picture by it.
        session = str(found.get("session") or "") or None
        return await relay.vnc_socket_url(mode=mode, session=session)

    async def current_page_url(self, user_id: UUID, *, origin: str) -> str | None:
        """What page the browser signing in to `origin` is actually showing.

        VNC carries no navigation signal of its own -- it is pixels, not
        events -- so the anti-phishing host display on the sign-in page
        (`sign-in-to-site/[conversationId]/[toolCallId]/page.tsx`) polls
        this rather than reading it off the video the way the JSON stream's
        `onNavigated` used to. `None` when nothing can be read, which leaves
        that page showing the origin it was told about rather than breaking.
        """
        try:
            relay = await self._relay(user_id, start=False)
            found = await relay.targets(domain=host_of(origin))
        except SandboxCapabilityUnsupported:
            return None
        except SandboxUnavailable, BrowserRelayUnavailable:
            return None
        except OSError, httpx.HTTPError, ProviderGone, _engine_error():
            return None
        if not found:
            return None
        url = found[0].get("url")
        return str(url) if url else None

    async def resize_display(self, user_id: UUID, *, width: int, height: int) -> str:
        """Fit the display to the pane somebody is watching it in.

        `start=False`: this follows a pane that is already open, so it must
        not be what wakes a sandbox. A resize with nothing to resize is not
        an error worth raising at a viewer -- the caller turns the refusal
        into "keep what you have", which is a worse fit rather than a broken
        picture.

        One display serves every session in the sandbox, so this is not
        session-scoped and the last request wins. `/vnc`'s docstring records
        per-session displays as the real answer.
        """
        relay = await self._relay(user_id, start=False)
        return await relay.resize_display(width=width, height=height)

    async def reset_display(self, user_id: UUID) -> str:
        """Put the display back to its resting size.

        Called when the last viewer disconnects. `start=False`, because a
        paused sandbox has no display to reset and waking one to tidy it up
        would be the opposite of the point.
        """
        relay = await self._relay(user_id, start=False)
        return await relay.reset_display()

    async def signed_in_sites(
        self, user_id: UUID, *, wake: bool = False
    ) -> ProfileCookies:
        """Which hosts the browser holds cookies for, and nothing else.

        `wake` off by default: rendering a settings page must not be what
        starts somebody's computer, so a paused sandbox answers
        `running: False` and an empty list instead. No cookie value crosses
        this boundary -- see `browser_relay/cookies.py`.
        """
        relay = await self._relay(user_id, start=wake)
        if wake:
            # Three things have to be up, and `wake` means all three: the
            # sandbox, the relay process inside it, and Chrome. Starting only
            # the first left this answering "asleep" about a machine that was
            # plainly running -- `_relay` delivers the token but starts
            # nothing, and `health(start=True)` is the only thing that runs
            # the relay's own start script.
            await relay.health(start=True)
        return await relay.profile_cookies(start=wake)

    async def forget_sites(
        self, user_id: UUID, *, domains: list[str], sites: list[str]
    ) -> int:
        """Drop the cookies for these hosts, and say how many went.

        Unlike the delete this replaces, which removed Lemma's encrypted copy
        and left the browser signed in, this signs the browser out.

        `sites` are the registrable domains those hosts roll up to. They go
        in the same call so the "they signed in here" mark leaves with the
        cookies rather than outliving them.
        """
        relay = await self._relay(user_id, start=True)
        await relay.health(start=True)
        await _require_private(relay, doing="forget a saved login")
        return await relay.forget_cookies(domains=domains, sites=sites)

    async def mark_signed_in(self, user_id: UUID, *, site: str) -> None:
        """Record that somebody said they signed in to this site.

        The one fact about a login that cannot be read back off the profile.
        Measured: `api.asur.work`'s two session cookies and `youtube.com`'s
        six visitor cookies are indistinguishable by every flag CDP reports,
        so without this the list can only say "sites with cookies". See
        `sandbox_runtime/browser_relay/marks.py`.

        `start=False`: the browser has just been driven through a sign-in,
        so the relay is up -- and if it is not, a lost label must not be
        what fails a sign-in that worked.
        """
        relay = await self._relay(user_id, start=False)
        await relay.mark_signed_in(site=site)

    async def ensure_for_sign_in(
        self,
        user_id: UUID,
        *,
        origin: str,
        report: bool = False,
    ) -> dict[str, object] | None:
        """Put the site in front of the person before they arrive.

        Called when a sign-in is asked for, not when the person opens the link:
        by the time they arrive the browser may have retired for idleness, so
        this is a best effort that the arrival repeats. What it buys is the
        common case where they click straight away.

        `report` returns where the browser landed -- address and page title --
        which is how `already_signed_in` decides whether the person needs
        asking at all. Off by default because the other callers are opening a
        page for a person, not asking a question about it.
        """
        relay = await self._relay(user_id, start=True)
        await _require_private(relay, doing="sign in to a site")
        # The one browser, the one anybody watching is already looking at.
        # This used to open a Chrome named for the site, so that a capture
        # taken from it could only contain that site -- scoping by
        # construction, and the reason a sign-in then had to be carried into
        # the agent's own browser afterwards. Nothing is captured now.
        landed = await relay.ensure_browser(origin=origin, session=None, domain=None)
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

    So all three paths into that browser refuse: loading a saved login, opening
    one for a sign-in, and attaching a viewer -- the last because attaching is
    also how somebody drives, and a password typed into a browser behind an
    open dashboard is the same exposure as a session loaded into one. An
    earlier draft of this argued that watching was safe because the viewer
    arrives over an authenticated socket. That is true of the socket and beside
    the point: what leaks is the sandbox's own address, which nothing about the
    viewer's credentials closes.
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
