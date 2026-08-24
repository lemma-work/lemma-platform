from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent_surfaces.platforms.common import UnsafeApiBaseError


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

    The partial guard, and deliberately so. Every other surface checks its
    `api_base_url` with `assert_safe_url`, which resolves the host — and cannot
    be used here, because it is async and this is the synchronous constructor
    thirty call sites reach for. Making it async is its own change.

    What this does close is the case worth closing first: the cloud metadata
    service has no hostname anybody uses. It is reached as the literal
    169.254.169.254, and a literal needs no DNS to recognise. Loopback and the
    RFC1918 ranges come free with the same check.

    What it does not close: a *name* that resolves somewhere private. That
    needs resolution, so it needs the async guard. Tracked as DEV-SURF-003.
    """
    if not base_url:
        return base_url
    host = urlsplit(base_url).hostname or ""
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return base_url  # a name; only the async guard can judge it
    if (
        address.is_link_local
        or address.is_loopback
        or address.is_private
        or address.is_reserved
        or address.is_multicast
    ):
        raise UnsafeApiBaseError(
            "Refusing to call the Slack API at an address that is not routable "
            "on the public internet.",
            reason="link_local_address" if address.is_link_local else "private_address",
        )
    return base_url


def build_slack_client(credentials: dict[str, Any]) -> AsyncWebClient:
    kwargs: dict[str, Any] = {}
    token = slack_access_token(credentials)
    if token:
        kwargs["token"] = token
    base_url = slack_base_url(credentials)
    if base_url:
        kwargs["base_url"] = base_url
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
