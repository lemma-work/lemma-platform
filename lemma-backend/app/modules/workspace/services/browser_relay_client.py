"""Talking to the browser relay inside a sandbox.

The one place that knows the relay exists. Everything above it -- the view
controller, the sign-in flow -- asks this for a browser, a session, or a socket,
and never learns which fabric the sandbox is on.

The token is derived, not stored: HMAC of a configured key over the sandbox's
provider id, the same arrangement the workspace runtime uses. That means
delivering it again after a resume produces the same value, so re-delivery is
idempotent and there is no per-sandbox secret to keep in a table and rotate.
"""

from __future__ import annotations

from typing import TypedDict

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from urllib.parse import quote
from uuid import UUID

import httpx

from sandbox_runtime.paths import WORKSPACE_ROOT
from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers.base import (
    ProviderCapability,
    ProviderInstance,
    SandboxEndpoint,
    require_capability,
)
from app.modules.workspace.providers.profiles import WORKSPACE_BROWSER_RELAY_PORT
from app.modules.workspace.services.browser_proxy import (
    BROWSER_PROXY_DECISION_PATH,
    browser_proxy_for,
    decision_bytes,
)
from sandbox_runtime.errors import SandboxCapabilityUnsupported

logger = get_logger(__name__)

#: Where the relay reads its token. Matches `browser_relay/app.py`, and is
#: deliberately outside `/tmp/lemma-browser` so quiesce does not take it.
RELAY_TOKEN_PATH = "/tmp/lemma-relay/token"

RELAY_TOKEN_HEADER = "X-Lemma-Relay-Token"

#: A cold browser start is minutes on an emulated image; `browser:ensure` is
#: allowed to take that long because the alternative is telling somebody their
#: browser will not start while it is still coming up.
_ENSURE_TIMEOUT_SECONDS = 300.0
_QUICK_TIMEOUT_SECONDS = 30.0


class BrowserRelayUnavailable(RuntimeError):
    """The relay did not answer, or this image does not have one."""


def relay_token(provider_id: str) -> str:
    """The shared secret for one sandbox's relay.

    Derived rather than random so that re-delivering it to a resumed sandbox
    yields the same value -- the sandbox may have been paused for a week and
    come back with the file intact, or come back without it, and both have to
    work without anyone recording which.
    """
    key = workspace_settings.runtime_credential_key
    if not key:
        raise BrowserRelayUnavailable(
            "no runtime credential key is configured, so no relay token can be made"
        )
    return hmac.new(
        key.encode(), f"browser-relay:{provider_id}".encode(), hashlib.sha256
    ).hexdigest()


class ProfileCookie(TypedDict):
    """One cookie, as much of it as ever leaves the sandbox.

    Host and expiry. Deliberately not the name and emphatically not the
    value: this is enough to say "you are signed in to example.com" and
    enough to delete it, and not enough to be anybody anywhere.
    """

    domain: str
    expires: float | None


class ProfileCookies(TypedDict):
    """What the browser is holding, or that it is not running to be asked.

    `signed_in` is the set of sites somebody answered "yes, I signed in" to.
    It comes from a file beside the profile rather than from the cookies,
    because no flag on a cookie distinguishes a session from visitor
    tracking -- see `sandbox_runtime/browser_relay/marks.py`. Present even
    when the browser is not running, since reading it costs no browser.
    """

    running: bool
    cookies: list[ProfileCookie]
    signed_in: list[str]


#: Bring the display stack up, on this image or on the one before it.
#:
#: `lemma-ensure-display` is `start-browser` renamed. The rename ships in the
#: image and the image rollout is deliberately deferred, so for as long as
#: that gap is open a backend that only knew the new name would fail to start
#: the browser at all on every sandbox still running the old one -- not
#: degrade, fail: the command does not exist, the relay never comes up, and
#: the viewer gets "the browser relay did not start".
#:
#: Falling back costs one `command -v`. It comes out when the images are
#: rolled, and until then this is the difference between a deploy that is
#: safe in either order and one that is not.
#:
#: `start-vnc-bridge` is the viewing half -- x11vnc and websockify, 66 MiB
#: measured -- which `lemma-ensure-display` no longer starts, because an
#: agent doing research pays for it and nobody is watching. This is the
#: viewer's own path, so this is where it is asked for. Guarded by
#: `command -v` for the same rollout reason as the line above: on an image
#: that predates the split, the two are already running and there is
#: nothing to start.
_ENSURE_DISPLAY = (
    "if command -v lemma-ensure-display >/dev/null 2>&1; then "
    "  lemma-ensure-display; "
    "else "
    "  start-browser; "
    "fi; "
    "if command -v start-vnc-bridge >/dev/null 2>&1; then "
    "  start-vnc-bridge; "
    "fi"
)


class BrowserRelayClient:
    """One sandbox's relay, reached through whatever door its fabric has."""

    def __init__(self, provider, instance: ProviderInstance) -> None:
        self._provider = provider
        self._instance = instance
        self._token = relay_token(instance.provider_id)

    async def _endpoint(self, *, deadline_seconds: float) -> SandboxEndpoint:
        require_capability(self._provider, ProviderCapability.PORT_REACH)
        deadline = datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds)
        return await self._provider.reach_port(
            self._instance, port=WORKSPACE_BROWSER_RELAY_PORT, deadline_at=deadline
        )

    async def deliver_browser_proxy(self, sandbox_id: UUID, kind: SandboxKind) -> None:
        """Tell the sandbox whether to proxy its browser, and through what.

        Written on every use, like the token above and for the same reason:
        asking is more expensive than writing, and a resumed sandbox's
        filesystem may or may not still carry it.

        Always written, including when the answer is "no proxy" -- an empty
        file is how a withdrawal reaches a sandbox that already has one. A
        decision that is merely absent means the server has not spoken, and
        an older sandbox with a baked environment variable would go on using
        it.

        Delivered as a secret, so the URL never appears in a command line.
        It is readable by the agent's own shell, which runs as the same
        user; that is the same exposure the environment variable already
        had, and it is why a credential put here should be scoped to this.
        """
        require_capability(self._provider, ProviderCapability.SECRET_DELIVERY)
        deadline = datetime.now(timezone.utc) + timedelta(
            seconds=_QUICK_TIMEOUT_SECONDS
        )
        await self._provider.deliver_secret(
            self._instance,
            path=BROWSER_PROXY_DECISION_PATH,
            value=decision_bytes(browser_proxy_for(sandbox_id, kind)),
            deadline_at=deadline,
        )

    async def deliver_token(self) -> None:
        """Put the token where the relay reads it.

        Called before the first use in a session rather than at create: a
        sandbox that has been resumed has a filesystem that may or may not still
        carry it, and asking is more expensive than writing.
        """
        require_capability(self._provider, ProviderCapability.SECRET_DELIVERY)
        deadline = datetime.now(timezone.utc) + timedelta(
            seconds=_QUICK_TIMEOUT_SECONDS
        )
        await self._provider.deliver_secret(
            self._instance,
            path=RELAY_TOKEN_PATH,
            value=self._token.encode(),
            deadline_at=deadline,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
        timeout: float = _QUICK_TIMEOUT_SECONDS,
    ) -> httpx.Response:
        endpoint = await self._endpoint(deadline_seconds=timeout)
        headers = {**endpoint.headers, RELAY_TOKEN_HEADER: self._token}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.request(
                    method,
                    f"{endpoint.url.rstrip('/')}{path}",
                    headers=headers,
                    params=params,
                    json=json_body,
                )
        except httpx.HTTPError as exc:
            # An image built before the relay existed answers nothing on this
            # port. Said as its own sentence because the remedy is different:
            # not "retry", but "this sandbox is running an older image".
            raise BrowserRelayUnavailable(
                f"the browser relay did not answer: {type(exc).__name__}"
            ) from exc

    async def health(self, *, start: bool = False) -> str:
        """`running`, `stopped`, or raises if the relay itself is not there.

        `start` runs the relay's own start script first if nothing answers.
        Through `start_process`, which every provider implements -- so the
        same one call brings the relay up on Docker's runtime, on E2B's SDK
        and in the desktop guest, with no per-fabric branch and no start
        command baked into an image that has none.

        "Nothing answers" is two different things depending on the fabric, and
        for a while this only knew one of them. On Docker a port with no
        listener refuses the connection, so `_request` raises. On E2B nothing
        refuses: the edge is always there and answers for the sandbox, so an
        unopened port comes back as a perfectly valid `502`. That took the
        `except` out of the picture entirely -- `ensure_running` was never
        reached, and the relay could not be started on E2B at all.
        """
        try:
            response = await self._request("GET", "/health")
            if _nothing_is_listening(response):
                raise BrowserRelayUnavailable(
                    f"the browser relay answered {response.status_code}"
                )
        except BrowserRelayUnavailable:
            if not start:
                raise
            await self.ensure_running()
            response = await self._request("GET", "/health")
        if response.status_code != 200:
            raise BrowserRelayUnavailable(
                f"the browser relay answered {response.status_code}"
            )
        return str(response.json().get("chrome", "stopped"))

    async def viewers(self) -> int | None:
        """How many sockets this sandbox's relay is serving, or None.

        None when the relay cannot say -- an older image, or one that is not
        answering. The caller treats that as "cannot tell" rather than as
        zero, because acting on a guess here resizes a display somebody is
        looking at.
        """
        response = await self._request("GET", "/health", timeout=10.0)
        if response.status_code != 200:
            return None
        body = response.json()
        count = body.get("viewers") if isinstance(body, dict) else None
        return count if isinstance(count, int) else None

    async def ensure_running(self) -> None:
        """Bring up everything a viewer needs, and wait for it to answer.

        Started on demand rather than with the sandbox because it is only
        wanted by somebody looking at a browser, and a workspace that never
        opens a page should not carry Xvfb, x11vnc, websockify and a relay
        for the life of the container.

        `lemma-ensure-display` rather than the relay's own start script: the
        relay answering has never meant a viewer would get a picture. That
        is `x11vnc` in front of Xvfb with `websockify` in front of it, none
        of which is the relay, and all of which used to arrive as a side
        effect of whatever browser command happened to run first. So a
        viewer's own path asks for them, and the wait below is not satisfied
        until the relay says they are listening -- which is how a display
        that failed to come up reports itself as that, rather than as "the
        browser is not running".

        Idempotent throughout: every piece is guarded by a `pgrep`, so this
        is a check on a warm sandbox and a start on a cold one.
        """
        from uuid import uuid4

        from sandbox_runtime.protocol import StartProcessRequest

        deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
        await self._provider.start_process(
            self._instance,
            StartProcessRequest(
                operation_id=uuid4(),
                shell_command=_ENSURE_DISPLAY,
                argv=None,
                cwd=WORKSPACE_ROOT,
                environment=(),
                tty=None,
                output_limit_bytes=4096,
                deadline_at=deadline,
            ),
            deadline_at=deadline,
        )

        # uvicorn binds in well under a second; this bound is for a container
        # still finding its feet, not for a healthy start. Xvfb and x11vnc
        # are slower on a cold E2B sandbox, which is what the second half of
        # this budget is for.
        answered = False
        for _ in range(80):
            await asyncio.sleep(0.25)
            with suppress(BrowserRelayUnavailable):
                response = await self._request("GET", "/health")
                if response.status_code != 200:
                    continue
                answered = True
                body = response.json()
                vnc = body.get("vnc") if isinstance(body, dict) else None
                # `None` is an older image's relay, which reports no `vnc` at
                # all. Taking its silence as failure would wait out the whole
                # budget and then refuse a sandbox that works.
                if vnc != "down":
                    return
        if answered:
            raise BrowserRelayUnavailable(
                "the display came up but nothing is serving it: "
                "x11vnc or websockify did not start"
            )
        raise BrowserRelayUnavailable("the browser relay did not start")

    async def ensure_browser(
        self,
        *,
        origin: str | None = None,
        session: str | None = None,
        domain: str | None = None,
    ) -> dict[str, object]:
        """Start the browser if needed, put it on `origin`, and say which page.

        The origin is what makes a person arriving at a link land on the site
        they were told about. Without it they get whatever the browser last had
        open, which after an idle retirement is a blank page.

        The reply names the session the target belongs to. Callers must use
        that rather than working the name out again: a target id is only
        meaningful against the Chrome that minted it.
        """
        await self.health(start=True)
        response = await self._request(
            "POST",
            "/browser:ensure",
            json_body={"origin": origin, "session": session, "domain": domain},
            timeout=_ENSURE_TIMEOUT_SECONDS,
        )
        if response.status_code == 409:
            raise BrowserRelayUnavailable(_detail(response))
        if response.status_code != 200:
            raise BrowserRelayUnavailable(
                f"the browser relay answered {response.status_code}"
            )
        return response.json()

    async def reset_display(self) -> str:
        """Put the display back to the size the image starts it at."""
        response = await self._request("POST", "/display:reset", timeout=30.0)
        if response.status_code != 200:
            raise BrowserRelayUnavailable(_detail(response))
        return str(response.json().get("size") or "")

    async def profile_cookies(self, *, start: bool = False) -> ProfileCookies:
        """Which hosts the browser holds cookies for, with no values.

        Never starts a browser: a sandbox that is asleep answers
        `running: False`, which is a state a settings page can render rather
        than an error it has to explain.
        """
        response = await self._request(
            "GET",
            "/profile:cookies",
            params={"start": "true"} if start else None,
            # Long enough to cover a cold Chrome, which `start` may have to
            # wait for.
            timeout=120.0 if start else 30.0,
        )
        if response.status_code != 200:
            raise BrowserRelayUnavailable(_detail(response))
        body = response.json()
        if not isinstance(body, dict):
            return {"running": False, "cookies": [], "signed_in": []}
        return {
            "running": bool(body.get("running")),
            "signed_in": [
                str(site)
                for site in body.get("signed_in") or []
                if isinstance(site, str)
            ],
            "cookies": [
                {
                    "domain": str(cookie.get("domain") or ""),
                    "expires": (
                        float(cookie["expires"])
                        if isinstance(cookie.get("expires"), (int, float))
                        else None
                    ),
                }
                for cookie in body.get("cookies") or []
                if isinstance(cookie, dict) and cookie.get("domain")
            ],
        }

    async def mark_signed_in(self, *, site: str) -> None:
        """Record that somebody said they signed in to this site.

        Best effort by the caller's choosing, not by this method's: it
        raises like everything else here, and the sign-in flow decides that
        a lost label must not fail a sign-in that worked.
        """
        response = await self._request(
            "POST",
            "/profile:signed-in",
            json_body={"site": site},
            timeout=30.0,
        )
        if response.status_code != 200:
            raise BrowserRelayUnavailable(_detail(response))

    async def forget_cookies(self, *, domains: list[str], sites: list[str]) -> int:
        """Drop every cookie set for these hosts. Returns how many went.

        `sites` are the registrable domains those hosts roll up to, so the
        "they signed in here" marks go in the same call. Passed rather than
        derived: grouping is a public-suffix question and that list lives on
        this side of the boundary.
        """
        response = await self._request(
            "POST",
            "/profile:forget",
            json_body={"domains": domains, "sites": sites},
            timeout=60.0,
        )
        if response.status_code != 200:
            raise BrowserRelayUnavailable(_detail(response))
        dropped = response.json().get("dropped")
        return int(dropped) if isinstance(dropped, int) else 0

    async def vnc_socket_url(
        self, *, mode: str, session: str | None = None
    ) -> tuple[str, dict[str, str]]:
        """Where to attach for a VNC view of the sandbox's whole display.

        No target in the query: VNC shows the shared Xvfb display rather than
        one CDP-selected tab, so there is nothing to name. `session` is not a
        selector either -- it cannot be, for the same reason -- it is only
        which name the driving lease is recorded under, so the agent's own
        script (which checks a lease for *its* session before acting) is not
        told a login session's wheel is free while a person is visibly
        driving it on this same shared screen. Callers pass the session
        `ensure_browser` actually resolved, not one they derive themselves.
        """
        endpoint = await self._endpoint(deadline_seconds=_QUICK_TIMEOUT_SECONDS)
        base = endpoint.url.rstrip("/")
        scheme = "wss" if base.startswith("https") else "ws"
        host = base.split("://", 1)[-1]
        query = f"mode={quote(mode, safe='')}"
        if session:
            query += f"&session={quote(session, safe='')}"
        headers = {**endpoint.headers, RELAY_TOKEN_HEADER: self._token}
        return f"{scheme}://{host}/vnc?{query}", headers

    async def targets(self, *, domain: str | None = None) -> list[dict[str, object]]:
        """Open pages in one session, named the same way `ensure_browser` is.

        Used to read back the page a VNC-attached browser is actually on --
        VNC carries no signal of its own for that, unlike the JSON stream
        this replaced, which sent a `url` message on every navigation.
        """
        path = f"/targets?domain={quote(domain, safe='')}" if domain else "/targets"
        response = await self._request("GET", path)
        if response.status_code != 200:
            raise BrowserRelayUnavailable(_detail(response))
        found = response.json().get("targets")
        return found if isinstance(found, list) else []

    async def resize_display(self, *, width: int, height: int) -> str:
        """Make the sandbox display the shape of the pane watching it.

        Returns the size it settled on, which is not always the size asked
        for: the framebuffer Xvfb allocated at startup is a ceiling RandR
        cannot raise, so a large request is clamped rather than refused.
        """
        response = await self._request(
            "POST", "/display:resize", json_body={"width": width, "height": height}
        )
        if response.status_code != 200:
            raise BrowserRelayUnavailable(_detail(response))
        size = response.json().get("size")
        return str(size) if size else ""

    async def endpoint_is_public(self) -> bool:
        """Whether this sandbox's ports are on the internet behind only a token."""
        endpoint = await self._endpoint(deadline_seconds=_QUICK_TIMEOUT_SECONDS)
        return endpoint.public


def _nothing_is_listening(response: httpx.Response) -> bool:
    """Whether this answer means "no process has the port", not "the relay said no".

    E2B's edge answers for the sandbox whether or not anything is bound, and
    says which case it is in the body::

        502 {"message": "The sandbox is running but port is not open", ...}

    So the `502` is the fabric's way of spelling what a connection refusal
    spells on Docker, and both have to reach `ensure_running`. Only `502`, and
    deliberately not every 5xx: a relay that is up and failing should be
    reported, not silently restarted, and the second probe after a start
    attempt still surfaces whatever it finds.
    """
    return response.status_code == 502


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"the browser relay answered {response.status_code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail or f"the browser relay answered {response.status_code}")


__all__ = [
    "BrowserRelayClient",
    "BrowserRelayUnavailable",
    "RELAY_TOKEN_HEADER",
    "RELAY_TOKEN_PATH",
    "SandboxCapabilityUnsupported",
    "relay_token",
]
