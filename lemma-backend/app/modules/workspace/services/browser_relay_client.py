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

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import hmac

import httpx

from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.providers.base import (
    ProviderCapability,
    ProviderInstance,
    SandboxEndpoint,
    require_capability,
)
from app.modules.workspace.providers.profiles import WORKSPACE_BROWSER_RELAY_PORT
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
        """
        try:
            response = await self._request("GET", "/health")
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

    async def ensure_running(self) -> None:
        """Start the relay process, and wait for it to answer.

        Started on demand rather than with the sandbox because it is only
        wanted by somebody looking at a browser, and a workspace that never
        opens a page should not carry the process.
        """
        from uuid import uuid4

        from sandbox_runtime.protocol import StartProcessRequest

        deadline = datetime.now(timezone.utc) + timedelta(seconds=60)
        await self._provider.start_process(
            self._instance,
            StartProcessRequest(
                operation_id=uuid4(),
                shell_command="start-browser-relay",
                argv=None,
                cwd="/workspace",
                environment=(),
                tty=None,
                output_limit_bytes=4096,
                deadline_at=deadline,
            ),
            deadline_at=deadline,
        )

        # uvicorn binds in well under a second; this bound is for a container
        # still finding its feet, not for a healthy start.
        for _ in range(40):
            await asyncio.sleep(0.25)
            with suppress(BrowserRelayUnavailable):
                response = await self._request("GET", "/health")
                if response.status_code == 200:
                    return
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

    async def save_state(self, *, domain: str) -> dict[str, object]:
        """Whatever the login session for this site is signed in to."""
        response = await self._request(
            "POST", "/state:save", json_body={"domain": domain}, timeout=120.0
        )
        if response.status_code != 200:
            raise BrowserRelayUnavailable(_detail(response))
        state = response.json().get("state")
        return state if isinstance(state, dict) else {}

    async def load_state(self, state: dict[str, object], *, domain: str) -> None:
        response = await self._request(
            "POST",
            "/state:load",
            json_body={"state": state, "domain": domain},
            timeout=120.0,
        )
        if response.status_code not in (200, 204):
            raise BrowserRelayUnavailable(_detail(response))

    async def clear_state(self, *, domain: str) -> None:
        """Take a login session out of the browser when a run is done with it."""
        try:
            await self._request(
                "POST", "/state:clear", json_body={"domain": domain}, timeout=60.0
            )
        except BrowserRelayUnavailable:
            # Best effort by design: the sandbox may already be gone, which is
            # the outcome this wanted. Logged rather than raised so a caller
            # finishing a run is not failed by cleanup.
            logger.warning("workspace.browser_relay.clear_failed.degraded")

    async def session_socket_url(
        self, *, target_id: str, mode: str, session: str | None = None
    ) -> tuple[str, dict[str, str]]:
        """Where to attach for one viewer, and the headers to attach with."""
        endpoint = await self._endpoint(deadline_seconds=_QUICK_TIMEOUT_SECONDS)
        base = endpoint.url.rstrip("/")
        scheme = "wss" if base.startswith("https") else "ws"
        host = base.split("://", 1)[-1]
        query = f"target={target_id}&mode={mode}"
        if session:
            query += f"&session={session}"
        headers = {**endpoint.headers, RELAY_TOKEN_HEADER: self._token}
        return f"{scheme}://{host}/session?{query}", headers

    async def endpoint_is_public(self) -> bool:
        """Whether this sandbox's ports are on the internet behind only a token."""
        endpoint = await self._endpoint(deadline_seconds=_QUICK_TIMEOUT_SECONDS)
        return endpoint.public


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
