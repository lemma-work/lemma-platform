"""Fetch a public page as a real browser would, without being one.

Sites that gate scripted clients do it on the TLS handshake -- the JA3/JA4
fingerprint of the cipher list, extension order and HTTP/2 SETTINGS frame -- and
that happens *before* the first header is read. So a browser-shaped
``User-Agent`` cannot help: by the time the server sees it, it has already
decided. Measured on fifteen ordinary research URLs, the plain client got seven;
this one gets ten, and the three it rescues (Britannica, Reuters, Medium) each
used to cost a ~5s browser render in the sandbox to read a static article.

``curl_cffi`` is libcurl patched to replay a real Chrome fingerprint, which is
what closes that gap. It is a genuinely async client, so nothing here needs
:func:`app.core.concurrency.offload.run_blocking`.

This grants no reach the platform did not already have -- the sandbox runs a
headed Chrome against these same URLs on the fallback path. It just stops us
starting a browser to read an article.

Redirects are followed *here*, one hop at a time, with
:func:`app.core.net.url_guard.assert_safe_url` re-run on every hop, because a
public URL answering ``302 -> 169.254.169.254`` is the obvious way past a check
that only looked at what the caller typed. That is the same contract
:func:`app.core.net.url_guard.fetch_guarded` provides for httpx; it could not be
reused directly because it is written against ``httpx.Response``. The validation
itself is not reimplemented -- only the loop is.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from app.core.config import settings
from app.core.log.log import get_logger
from app.core.net.url_guard import GuardPolicy, UnsafeUrlError, assert_safe_url

if TYPE_CHECKING:
    from curl_cffi.requests import AsyncSession

logger = get_logger(__name__)

# curl_cffi is imported where it is used, not at module scope. It pulls in ~60
# modules through cffi, and `web_fetch` is a tool the agent reaches for
# occasionally -- not something on the path to serving a request. Paying that at
# every process start put the import budget over its tolerance. `url_guard` defers
# httpx for the same reason.
_session: "AsyncSession | None" = None

# The fingerprint to wear. "chrome" tracks the newest Chrome curl_cffi knows,
# which is the point: a pinned old version is itself a tell.
_IMPERSONATE = "chrome"

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class HttpStatusError(RuntimeError):
    """The page came back with a non-success status."""

    def __init__(self, status_code: int):
        super().__init__(f"The site returned HTTP {status_code}.")
        self.status_code = status_code


class PageUnreachableError(RuntimeError):
    """The page could not be retrieved at all — DNS, TLS, connection, timeout.

    Exists so callers do not have to import curl_cffi's exception hierarchy to
    catch a failed fetch; the client that raised it is an implementation detail
    of this module.
    """


@dataclass(frozen=True)
class FetchedBody:
    body: bytes
    content_type: str | None
    final_url: str


def get_impersonating_client() -> "AsyncSession":
    """Return the process-wide impersonating client, creating it on first use.

    One session for the process, like :func:`app.core.net.http_client
    .get_shared_http_client`: building a libcurl session per call would throw
    away connection reuse and is what ``check_io_hygiene``'s
    ``process-lifetime-construction`` rule exists to catch.
    """
    from curl_cffi.requests import AsyncSession

    global _session
    if _session is None:
        _session = AsyncSession(max_clients=settings.outbound_http_max_connections)
    return _session


async def close_impersonating_client() -> None:
    """Close the shared session. Called from the API and worker shutdowns."""
    global _session
    if _session is not None:
        await _session.close()
    _session = None


def web_page_policy() -> GuardPolicy:
    """The guard policy for reading a public web page.

    Identical to the connector policy except that plain ``http`` stays allowed.
    ``GuardPolicy.from_settings`` turns http off outside development, which is
    right for a connector carrying credentials and wrong here: this fetch sends
    no secret, plenty of the web is still http, and refusing it would break
    pages agents read today. Everything with teeth -- the private-address rules,
    the port policy, the never-negotiable link-local rejection -- is inherited.
    """
    return replace(GuardPolicy.from_settings(), allow_http=True)


async def fetch_guarded_impersonated(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
    headers: dict[str, str] | None = None,
    max_redirects: int = 3,
    policy: GuardPolicy | None = None,
) -> FetchedBody:
    """GET ``url`` as Chrome, re-validating every hop and capping the body.

    Mirrors :func:`app.core.net.url_guard.fetch_guarded` -- same argument names,
    same :class:`UnsafeUrlError` reasons -- so the two read as siblings.

    The body is cut off *during* the transfer rather than measured afterwards: a
    host that advertises a small response and sends a large one should cost us
    ``max_bytes``, not its whole body.
    """
    from curl_cffi.requests.errors import RequestsError

    session = get_impersonating_client()
    policy = policy or web_page_policy()
    current = url
    for _ in range(max_redirects + 1):
        await assert_safe_url(current, policy=policy)
        try:
            async with session.stream(
                "GET",
                current,
                headers=headers or {},
                timeout=timeout,
                impersonate=_IMPERSONATE,
                allow_redirects=False,
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeUrlError(
                            "Redirect without a target.", reason="invalid_redirect"
                        )
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise HttpStatusError(response.status_code)
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_content():
                    total += len(chunk)
                    if total > max_bytes:
                        raise UnsafeUrlError(
                            f"Response exceeds the {max_bytes} byte limit.",
                            reason="response_too_large",
                        )
                    chunks.append(chunk)
                logger.debug(
                    "net.impersonating_client.fetch_completed.observed",
                    status_code=response.status_code,
                    bytes=total,
                )
                return FetchedBody(
                    body=b"".join(chunks),
                    content_type=response.headers.get("content-type"),
                    final_url=current,
                )
        except RequestsError as exc:
            raise PageUnreachableError(str(exc)) from exc
    raise UnsafeUrlError("Too many redirects.", reason="too_many_redirects")
