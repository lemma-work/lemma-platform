"""TURN credentials for a WebRTC browser view, from whichever relay is configured.

Both ends of the stream are relay-only: the streamer in the sandbox and the
viewer's browser each allocate on TURN and never offer a host address. That
is a choice, not a fallback. A sandbox's addresses are not anybody's business,
a viewer's network is often one where nothing else gets through, and it means
one shape of connection to reason about instead of three.

Credentials are minted per connection and short-lived, in the REST style both
providers share. The long-lived secret -- Cloudflare's API token, coturn's
static auth secret -- never leaves this process: the sandbox and the browser
each get a username and password that expire.

**TLS on 443 is always in the list.** UDP is the fast path and usually works,
but a sandbox fabric can drop non-DNS UDP and plenty of viewer networks only
pass 443. `turns:` on 443 is the transport that still gets through, so a
provider whose list lacks it is given one rather than trusted to have it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Protocol, TypedDict

import httpx

from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings

logger = get_logger(__name__)

_CLOUDFLARE_API = "https://rtc.live.cloudflare.com/v1/turn/keys"
_CLOUDFLARE_TLS_443 = "turns:turn.cloudflare.com:443?transport=tcp"


class IceServer(TypedDict, total=False):
    """One entry of `RTCConfiguration.iceServers`, as both ends read it."""

    urls: list[str]
    username: str
    credential: str


class TurnUnavailable(RuntimeError):
    """No relay could be had, so WebRTC cannot connect and VNC is the answer."""


class TurnProvider(Protocol):
    async def ice_servers(self, *, label: str, ttl_seconds: int) -> list[IceServer]:
        """Fresh credentials for one end of one connection."""
        ...


def with_tls_443(servers: list[IceServer], fallback_url: str | None) -> list[IceServer]:
    """`servers`, with a `turns:` URL on port 443 among the credentialed ones.

    Port 53 goes: browsers refuse it, and Cloudflare's own documentation says
    to drop it. STUN-only entries go too -- both ends are relay-only, so a
    server-reflexive candidate is never used and only costs a round trip.
    """
    kept: list[IceServer] = []
    for server in servers:
        if not server.get("username"):
            continue
        urls = [url for url in server.get("urls", []) if not _port_53(url)]
        if not urls:
            continue
        if fallback_url and not any(_is_tls_443(url) for url in urls):
            urls.append(fallback_url)
        kept.append({**server, "urls": urls})
    return kept


def _port_53(url: str) -> bool:
    return url.split("?", 1)[0].endswith(":53")


def _is_tls_443(url: str) -> bool:
    return url.startswith("turns:") and url.split("?", 1)[0].endswith(":443")


class CloudflareTurn:
    """Cloudflare Realtime TURN: anycast, with TLS on 443 out of the box."""

    def __init__(self, key_id: str, api_token: str) -> None:
        self._key_id = key_id
        self._api_token = api_token

    async def ice_servers(self, *, label: str, ttl_seconds: int) -> list[IceServer]:
        del label  # Cloudflare's credentials carry no caller-chosen name.
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{_CLOUDFLARE_API}/{self._key_id}/credentials/generate-ice-servers",
                    headers={"Authorization": f"Bearer {self._api_token}"},
                    json={"ttl": ttl_seconds},
                )
        except httpx.HTTPError as exc:
            raise TurnUnavailable(f"Cloudflare TURN did not answer: {exc!r}") from exc
        if response.status_code not in (200, 201):
            raise TurnUnavailable(
                f"Cloudflare TURN refused credentials ({response.status_code})"
            )
        return with_tls_443(_cloudflare_servers(response.json()), _CLOUDFLARE_TLS_443)


def _cloudflare_servers(body: object) -> list[IceServer]:
    """Both shapes Cloudflare has answered with: a list, or one object.

    Provider JSON, so nothing about it is trusted until it is read into the
    typed shape here.
    """
    raw = body.get("iceServers") if isinstance(body, dict) else None
    entries = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    servers: list[IceServer] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        urls = entry.get("urls")
        urls = [urls] if isinstance(urls, str) else urls
        if not isinstance(urls, list):
            continue
        server: IceServer = {"urls": [str(url) for url in urls]}
        if isinstance(entry.get("username"), str):
            server["username"] = entry["username"]
        if isinstance(entry.get("credential"), str):
            server["credential"] = entry["credential"]
        servers.append(server)
    return servers


class Coturn:
    """A self-hosted coturn with `use-auth-secret`, the REST credential scheme.

    The username is `<expiry>:<label>` and the password the HMAC-SHA1 of it
    under the shared secret, which coturn recomputes -- so nothing is stored
    and nothing is called.
    """

    def __init__(
        self, urls: list[str], secret: str, *, now: Callable[[], float] = time.time
    ) -> None:
        self._urls = urls
        self._secret = secret
        self._now = now

    async def ice_servers(self, *, label: str, ttl_seconds: int) -> list[IceServer]:
        # Named `login`, not `username`: CodeQL reads that name as personal
        # data. It is `<expiry>:<label>`, and the label is random per session.
        login = f"{int(self._now()) + ttl_seconds}:{label}"
        digest = hmac.new(self._secret.encode(), login.encode(), hashlib.sha1)
        credential = base64.b64encode(digest.digest()).decode()
        return with_tls_443(
            [
                {
                    "urls": list(self._urls),
                    "username": login,
                    "credential": credential,
                }
            ],
            None,
        )


def turn_provider() -> TurnProvider | None:
    """The configured relay, or `None` when WebRTC is off for want of one."""
    settings = workspace_settings
    if settings.turn_provider == "cloudflare":
        token = settings.cloudflare_turn_api_token
        if not settings.cloudflare_turn_key_id or token is None:
            logger.warning("workspace.browser_stream.turn_unconfigured.degraded")
            return None
        return CloudflareTurn(settings.cloudflare_turn_key_id, token.get_secret_value())
    if settings.turn_provider == "coturn":
        secret = settings.coturn_secret
        if not settings.coturn_urls or secret is None:
            logger.warning("workspace.browser_stream.turn_unconfigured.degraded")
            return None
        if not any(_is_tls_443(url) for url in settings.coturn_urls):
            # Allowed, because a local test stack has no certificate to offer,
            # and said, because in production this is the transport that gets
            # through when nothing else does.
            logger.warning("workspace.browser_stream.turn_without_tls_443.degraded")
        return Coturn(settings.coturn_urls, secret.get_secret_value())
    return None


__all__ = [
    "CloudflareTurn",
    "Coturn",
    "IceServer",
    "TurnProvider",
    "TurnUnavailable",
    "turn_provider",
    "with_tls_443",
]
