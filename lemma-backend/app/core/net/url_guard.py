"""Refuse outbound requests to addresses that are not the public internet.

Tenant-supplied connector targets -- an MCP server URL, an OpenAPI spec URL, a
SQL host -- are attacker-reachable input in a multi-tenant deployment. Without a
check, an organization admin can point an install at ``169.254.169.254`` and read
the cloud metadata service, or walk internal services on the cluster network,
with the response handed straight back to them.

Two things make this harder than matching on the hostname:

* **DNS.** ``internal.attacker.com`` can resolve to ``10.0.0.5``. The name tells
  you nothing, so every resolved address has to be checked -- all of them, since
  a host with both a public A record and a private AAAA record would otherwise
  pass on the record we happened to look at.
* **Redirects.** A public URL can 302 to a private one. Following redirects
  inside the HTTP client hides that hop entirely, which is why the shared client
  has redirects off and callers follow them here, re-checking each time.

This does not close the gap between validating a name and using it: DNS can
change in between (a rebind). Execution re-checks rather than trusting the
install-time result, which narrows the window to a single request; closing it
completely needs the resolved IP pinned into the connection, which the transport
does not expose cleanly.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.core.config import settings

_PUBLIC_SCHEMES = ("https",)
_LOCAL_SCHEMES = ("http", "https")
# Privileged ports are not connector targets, with the two obvious exceptions.
_ALLOWED_LOW_PORTS = {80, 443}
# Well-known non-HTTP services that happen to sit above 1024. Blocking by number
# alone would miss exactly the interesting ones -- Postgres, Redis, Elasticsearch
# and Mongo are all high ports and all things an SSRF wants to reach -- while
# blocking every high port would break legitimate connectors on 3000 or 8080.
_DENIED_SERVICE_PORTS = frozenset(
    {
        22,  # ssh
        23,  # telnet
        25,  # smtp
        110,  # pop3
        143,  # imap
        445,  # smb
        1433,  # mssql
        1521,  # oracle
        3306,  # mysql
        3389,  # rdp
        5432,  # postgres
        5672,  # amqp
        6379,  # redis
        9200,  # elasticsearch
        11211,  # memcached
        27017,  # mongodb
    }
)


class UnsafeUrlError(ValueError):
    """The target is not an address we are willing to send a request to."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class GuardPolicy:
    allow_private: bool = False
    allow_http: bool = False
    allow_unresolvable: bool = False

    @classmethod
    def from_settings(cls) -> "GuardPolicy":
        # Self-hosted deployments legitimately run connectors against their own
        # private network; opting in is explicit and off by default.
        allow_private = bool(
            getattr(settings, "connector_allow_private_network_targets", False)
        )
        is_dev = settings.environment in ("local", "testing")
        return cls(
            allow_private=allow_private,
            # Plain http is fine when the whole stack is a developer's laptop.
            allow_http=is_dev or allow_private,
            # In production an unresolvable host fails closed. Under test it does
            # not: suites drive mock transports against hostnames that were never
            # meant to resolve, and CI need not have outbound DNS. Literal
            # private addresses and anything that *does* resolve into a private
            # range are still rejected, so the guard keeps its teeth.
            allow_unresolvable=is_dev,
        )


def _is_disallowed_address(
    ip: ipaddress._BaseAddress, *, allow_private: bool = False
) -> str | None:
    """Return the reason this address is off-limits, or None if it is fine.

    ``allow_private`` is the self-hosting escape hatch, and it is deliberately
    narrower than "anything not public". A deployment that runs its own MCP
    server or database on an internal network needs RFC1918, ULA and loopback.
    It never needs 169.254.169.254, which is not a connector target at all --
    it is the cloud metadata service, and the single most valuable thing an
    SSRF can reach. So link-local stays denied even with the hatch open, and
    turning it on to reach your own subnet does not also hand out credentials
    for the instance.
    """
    if ip.is_link_local:
        # 169.254.0.0/16 and fe80::/10 -- checked first because link-local also
        # reports as private, and this rejection is not negotiable.
        return "link_local_address"
    if ip.is_multicast:
        return "multicast_address"
    if ip.is_unspecified:
        return "reserved_address"
    if allow_private:
        return None
    if ip.is_loopback:
        return "loopback_address"
    if ip.is_private:
        return "private_address"
    if ip.is_reserved:
        return "reserved_address"
    return None


async def _resolve_all(
    host: str, port: int, *, allow_unresolvable: bool
) -> list[ipaddress._BaseAddress]:
    # A bare IP literal needs no lookup, and must still be checked even when
    # resolution is permitted to fail.
    try:
        return [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        pass

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        if allow_unresolvable:
            return []
        raise UnsafeUrlError(
            f"Could not resolve host '{host}'.", reason="unresolvable_host"
        ) from exc
    addresses = []
    for info in infos:
        sockaddr = info[4]
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not addresses and not allow_unresolvable:
        raise UnsafeUrlError(
            f"Host '{host}' resolved to no usable address.", reason="unresolvable_host"
        )
    return addresses


async def assert_safe_url(url: str, *, policy: GuardPolicy | None = None) -> str:
    """Validate ``url`` as an outbound target, or raise :class:`UnsafeUrlError`."""
    policy = policy or GuardPolicy.from_settings()
    parsed = urlsplit(url.strip())

    allowed_schemes = _LOCAL_SCHEMES if policy.allow_http else _PUBLIC_SCHEMES
    if parsed.scheme not in allowed_schemes:
        raise UnsafeUrlError(
            f"URL scheme '{parsed.scheme or 'none'}' is not allowed "
            f"(expected {' or '.join(allowed_schemes)}).",
            reason="scheme_not_allowed",
        )
    if parsed.username or parsed.password:
        # Credentials in a URL end up in logs and redirect headers.
        raise UnsafeUrlError(
            "URL may not embed credentials.", reason="credentials_in_url"
        )
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL has no host.", reason="missing_host")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("URL has an invalid port.", reason="invalid_port") from exc
    privileged = port < 1024 and port not in _ALLOWED_LOW_PORTS
    if (privileged or port in _DENIED_SERVICE_PORTS) and not policy.allow_private:
        raise UnsafeUrlError(
            f"Port {port} is not an allowed connector target.",
            reason="port_not_allowed",
        )

    resolved = await _resolve_all(
        host, port, allow_unresolvable=policy.allow_unresolvable
    )
    for address in resolved:
        reason = _is_disallowed_address(address, allow_private=policy.allow_private)
        if reason:
            # The address itself is not echoed back: it is infrastructure detail
            # about wherever this happens to be deployed.
            raise UnsafeUrlError(
                f"Host '{host}' resolves to an address that is not routable on "
                "the public internet.",
                reason=reason,
            )
    return url


def _same_origin(left: str, right: str) -> bool:
    import httpx

    a, b = httpx.URL(left), httpx.URL(right)
    return (a.scheme, a.host, a.port) == (b.scheme, b.host, b.port)


def _redirected_request(
    method: str, status_code: int, body_kwargs: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """How a redirect rewrites the method and body, per RFC 9110.

    303 always becomes GET; 301/302 turn a POST into a GET (what every client
    does in practice); 307/308 preserve both. Getting this wrong would either
    replay a write against the new target or silently drop a body.
    """
    if status_code == 303 or (status_code in (301, 302) and method.upper() == "POST"):
        return "GET", {}
    return method, body_kwargs


async def request_guarded(
    client,
    method: str,
    url: str,
    *,
    headers: Any = None,
    params: Any = None,
    credential_header_names: Collection[str] = (),
    credential_param_names: Collection[str] = (),
    follow_redirects: bool = True,
    max_redirects: int = 3,
    policy: GuardPolicy | None = None,
    **kwargs: Any,
):
    """Issue a request, re-validating the target on every redirect hop.

    The shared client has redirects off process-wide because each new
    ``Location`` is a fresh target that has to be checked: a tenant-supplied URL
    on a host they control, answering ``302 -> 169.254.169.254``, is the obvious
    way around a check that only ran at install time. Letting the client follow
    redirects itself hands that target the request unguarded, which is the whole
    hole this closes.

    Credentials are dropped the moment a redirect leaves the original origin.
    The caller names which header and query keys carry them, because an OpenAPI
    spec can nominate any key as its auth carrier -- there is no fixed set to
    recognise -- and replaying a tenant's API key to whoever their own server
    redirects to would hand it away.
    """
    import httpx

    policy = policy or GuardPolicy.from_settings()
    origin = current = url
    current_method = method
    body_kwargs = kwargs
    dropped_headers = {name.lower() for name in credential_header_names}
    dropped_params = set(credential_param_names)

    for _ in range(max_redirects + 1):
        await assert_safe_url(current, policy=policy)
        if _same_origin(origin, current):
            request_headers, request_params = headers, params
        else:
            request_headers = {
                key: value
                for key, value in dict(headers or {}).items()
                if key.lower() not in dropped_headers
            }
            # `or None`, never an empty list: httpx rebuilds the URL's query
            # from whatever `params` it is given, so handing it `[]` erases the
            # query the URL already carries. A redirect target's query is often
            # the only thing authorizing it -- GitHub's archive download
            # redirects to a signed `codeload` URL -- and erasing it turns a
            # working download into a 404.
            request_params = [
                (key, value)
                for key, value in _as_param_pairs(params)
                if key not in dropped_params
            ] or None

        response = await client.request(
            current_method,
            current,
            headers=request_headers,
            params=request_params,
            follow_redirects=False,
            **body_kwargs,
        )
        # Raw passthrough opts out and hands the redirect back to the caller,
        # which is what it did before redirects were guarded here.
        if not response.is_redirect or not follow_redirects:
            return response

        location = response.headers.get("location")
        if not location:
            raise UnsafeUrlError(
                "Redirect without a target.", reason="invalid_redirect"
            )
        await response.aread()
        current = str(httpx.URL(current).join(location))
        current_method, body_kwargs = _redirected_request(
            current_method, response.status_code, body_kwargs
        )
        # The Location carries its own query string; re-appending the original
        # request's parameters to it would corrupt the target.
        params = None

    raise UnsafeUrlError("Too many redirects.", reason="too_many_redirects")


def _as_param_pairs(params: Any) -> list[tuple[str, Any]]:
    if not params:
        return []
    if isinstance(params, dict):
        return list(params.items())
    return list(params)


async def fetch_guarded(
    client,
    url: str,
    *,
    max_bytes: int,
    timeout: float,
    headers: dict[str, str] | None = None,
    max_redirects: int = 3,
    policy: GuardPolicy | None = None,
) -> bytes:
    """GET ``url``, re-validating every redirect hop and capping the body.

    Redirects are followed here rather than by the client because each new
    location is a fresh target that has to be checked -- a public URL that 302s
    to ``10.0.0.5`` is the obvious way around an install-time check. The body is
    streamed and cut off at ``max_bytes`` so a hostile endpoint cannot exhaust
    memory by advertising a small response and sending a large one.
    """
    import httpx

    policy = policy or GuardPolicy.from_settings()
    current = url
    for _ in range(max_redirects + 1):
        await assert_safe_url(current, policy=policy)
        async with client.stream(
            "GET", current, headers=headers or {}, timeout=timeout
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise UnsafeUrlError(
                        "Redirect without a target.", reason="invalid_redirect"
                    )
                current = str(httpx.URL(current).join(location))
                continue
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise UnsafeUrlError(
                        f"Response exceeds the {max_bytes} byte limit.",
                        reason="response_too_large",
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    raise UnsafeUrlError("Too many redirects.", reason="too_many_redirects")


async def assert_safe_host(
    host: str, port: int, *, policy: GuardPolicy | None = None
) -> str:
    """Validate a bare host/port pair, for targets that are not URLs (SQL)."""
    policy = policy or GuardPolicy.from_settings()
    if not host:
        raise UnsafeUrlError("No host supplied.", reason="missing_host")
    resolved = await _resolve_all(
        host, port, allow_unresolvable=policy.allow_unresolvable
    )
    for address in resolved:
        reason = _is_disallowed_address(address, allow_private=policy.allow_private)
        if reason:
            raise UnsafeUrlError(
                f"Host '{host}' resolves to an address that is not routable on "
                "the public internet.",
                reason=reason,
            )
    return host
