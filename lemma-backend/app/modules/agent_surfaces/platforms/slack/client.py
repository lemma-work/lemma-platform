from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent_surfaces.platforms.common import (
    UnsafeApiBaseError,
    assert_safe_api_base,
)
from app.modules.agent_surfaces.platforms.delivery import DeliveryClassification


def slack_access_token(credentials: dict[str, Any]) -> str | None:
    if credentials.get("access_token"):
        return str(credentials["access_token"])
    if credentials.get("bot_token"):
        return str(credentials["bot_token"])
    raw_response = credentials.get("raw_response") or {}
    token = raw_response.get("access_token")
    return str(token) if token else None


def slack_base_url(credentials: dict[str, Any]) -> str | None:
    if credentials.get("api_base_url"):
        return _refuse_a_private_literal(
            _normalize_base_url(str(credentials["api_base_url"]))
        )
    raw_response = credentials.get("raw_response") or {}
    value = raw_response.get("api_base_url")
    return _refuse_a_private_literal(_normalize_base_url(str(value))) if value else None


def _refuse_a_private_literal(base_url: str | None) -> str | None:
    """Reject a base URL that is *written* as a private address.

    Kept alongside the resolving guard in `build_slack_client` rather than
    replaced by it, because the two fail differently and the cheap one fails
    usefully: a literal needs no DNS to recognise, so this still refuses
    169.254.169.254 on a host with no resolver, or one whose resolver is being
    answered by somebody else.

    The case it cannot judge is a *name* that resolves somewhere private —
    `internal.attacker.example` pointed at 10.0.0.5. That needs resolution, and
    resolution is what `assert_safe_api_base` does at the point of use.
    """
    if not base_url:
        return base_url
    # The same escape hatch every other surface honours, through GuardPolicy:
    # a deployment running Slack against its own network says so, and this must
    # not be the one check that cannot be opted out of. Link-local stays
    # refused below regardless — the metadata service is never a Slack endpoint.
    from app.core.config import settings

    self_hosted = bool(
        getattr(settings, "connector_allow_private_network_targets", False)
    )
    host = urlsplit(base_url).hostname or ""
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return base_url  # a name; the resolving guard judges it
    if (
        address.is_link_local
        or address.is_multicast
        or (
            not self_hosted
            and (address.is_loopback or address.is_private or address.is_reserved)
        )
    ):
        raise UnsafeApiBaseError(
            "Refusing to call the Slack API at an address that is not routable "
            "on the public internet.",
            reason="link_local_address" if address.is_link_local else "private_address",
        )
    return base_url


async def build_slack_client(credentials: dict[str, Any]) -> AsyncWebClient:
    """A Slack client for these credentials, with its base URL vetted first.

    Async because vetting resolves DNS. `api_base_url` arrives from stored
    account credentials, which makes it tenant-supplied input, and a name
    pointed at internal infrastructure is the SSRF the literal check above
    cannot see. Every other surface — Gmail, Outlook, Resend, Telegram,
    WhatsApp — goes through `assert_safe_api_base` for exactly this; Slack was
    the one that did not, because this constructor used to be synchronous.
    """
    kwargs: dict[str, Any] = {}
    token = slack_access_token(credentials)
    if token:
        kwargs["token"] = token
    base_url = slack_base_url(credentials)
    if base_url:
        kwargs["base_url"] = await assert_safe_api_base(base_url, platform="Slack")
    return AsyncWebClient(**kwargs)


def slack_scopes(credentials: dict[str, Any]) -> set[str]:
    raw_values: list[str] = []
    for candidate in (
        credentials.get("scope"),
        credentials.get("scopes"),
        (credentials.get("raw_response") or {}).get("scope"),
        (credentials.get("raw_response") or {}).get("scopes"),
    ):
        if isinstance(candidate, str):
            raw_values.extend(candidate.split(","))
        elif isinstance(candidate, list):
            raw_values.extend(str(item) for item in candidate)
    return {value.strip() for value in raw_values if value and value.strip()}


def slack_supports_customized_messages(credentials: dict[str, Any]) -> bool:
    return "chat:write.customize" in slack_scopes(credentials)


def slack_customized_message_kwargs(
    credentials: dict[str, Any],
    agent_display_name: str | None,
    agent_icon_url: str | None = None,
) -> dict[str, Any]:
    """Author a message as the agent rather than as the app.

    Slack takes the name and the avatar together under ``chat:write.customize``;
    sending a name without a face leaves the app's generic icon next to a
    personal name, which reads as two different senders.
    """
    if not slack_supports_customized_messages(credentials):
        return {}
    normalized_name = str(agent_display_name or "").strip()
    if not normalized_name:
        return {}
    kwargs: dict[str, Any] = {"username": normalized_name}
    icon = str(agent_icon_url or "").strip()
    # Slack only accepts a publicly fetchable https icon; anything else would
    # fail the whole send, so a local/relative path is simply left off.
    if icon.startswith("https://"):
        kwargs["icon_url"] = icon
    return kwargs


def _normalize_base_url(value: str) -> str:
    return value if value.endswith("/") else f"{value}/"


def classify_slack_error(exc: Exception) -> DeliveryClassification:
    """Transient for 429 / 5xx / network errors; permanent for other 4xx.

    Same shape as ``classify_telegram_error`` and ``classify_whatsapp_error``,
    so all four platforms are retried by one policy. Slack reports a rate limit
    as ``SlackApiError`` with HTTP 429 and a ``Retry-After`` header, and
    ``SlackApiError`` is already a member of ``PLATFORM_TRANSPORT_ERRORS`` --
    so without this the answer was recorded as reaching nobody and dropped.
    """
    if isinstance(exc, SlackApiError):
        status = getattr(exc.response, "status_code", None)
        if status == 429 or (isinstance(status, int) and status >= 500):
            return DeliveryClassification.TRANSIENT
        return DeliveryClassification.PERMANENT
    if isinstance(exc, (aiohttp.ClientError, TimeoutError)):
        return DeliveryClassification.TRANSIENT
    return DeliveryClassification.PERMANENT


def slack_retry_after(exc: Exception) -> float | None:
    """Seconds Slack asked us to wait, from the ``Retry-After`` header."""
    if not isinstance(exc, SlackApiError):
        return None
    headers = getattr(exc.response, "headers", None) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    # aiohttp hands back a multi-dict; a repeated header arrives as a list.
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    try:
        seconds = float(raw)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None
    return seconds if seconds > 0 else None
