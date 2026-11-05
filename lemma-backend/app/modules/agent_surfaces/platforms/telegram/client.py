"""Unified Telegram Bot API transport for agent surfaces.

Single home for base-URL resolution, the ``bot{token}`` path quirk, the file
API URL quirk, error-envelope parsing (``ok`` / ``description`` /
``parameters.retry_after``), and failure classification. Adopted by the outbound
message service, the surface webhook-registration path, and the native polling
receiver's webhook teardown so they no longer keep divergent copies of the API
base URL and HTTP error handling. (The polling ``getUpdates`` long-poll stays
bespoke — it needs form encoding, a 35s timeout, and 409-conflict handling.)

Library evaluation (aiogram / python-telegram-bot): NOT adopted. This thin
client already covers everything the surface needs — ``sendMessage`` chunking,
``editMessageText`` progress streaming, ``sendChatAction`` typing, inline
keyboards, file upload/download, webhook registration, and transient/permanent
error classification with ``retry_after`` — in a small, dependency-free, fully
tested surface. A full-framework library would re-route all of that through a
different dispatcher/abstraction and re-introduce a heavy dependency for no
functional gain, so the raw client is retained intentionally.
"""

from __future__ import annotations

from app.modules.agent_surfaces.platforms.outbound_http import outbound_http

import json
from typing import Any

import httpx

from app.core.config import reveal_secret
from app.modules.agent_surfaces.domain.credentials import (
    CredentialsLike,
    TelegramCredentials,
)
from app.modules.agent_surfaces.platforms.common import assert_safe_api_base
from app.modules.agent_surfaces.platforms.delivery import (
    DeliveryClassification,
    classify_transport_failure,
)

# The one canonical Telegram API base. ``api_base_url`` in the bot credentials
# overrides it (used by tests to point at a fake server, and by self-hosted Bot
# API servers).
_TELEGRAM_API_BASE = "https://api.telegram.org/bot"

# Telegram rejects ``sendMessage`` text longer than this many UTF-16 code units.
TELEGRAM_MESSAGE_LIMIT = 4096

# Update types the surface cares about; shared between webhook registration and
# the polling receiver so both deliver identical updates. `my_chat_member` is the
# bot's own membership changing -- being added to a group, and by whom -- which
# is how a pod comes to know a group (see `services/group_registry`). A webhook
# registered before it was listed keeps the old set until it is registered again.
ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "my_chat_member"]


def resolve_api_base(credentials: CredentialsLike | None) -> str:
    """Resolve the Telegram API base, honoring a credential override."""
    return TelegramCredentials.of(credentials).api_base_url or _TELEGRAM_API_BASE


def normalize_bot_base_url(api_base: str | None, bot_token: str) -> str:
    """Return the ``.../bot{token}`` prefix for method calls (no trailing slash).

    Accepts a base that already ends with ``/bot{token}``, ends with ``/bot``,
    or is a bare host, so every call site resolves URLs identically.
    """
    base = (api_base or _TELEGRAM_API_BASE).rstrip("/")
    if base.endswith(f"/bot{bot_token}"):
        return base
    if base.endswith("/bot"):
        return f"{base}{bot_token}"
    return f"{base}/bot{bot_token}"


def file_api_url(api_base: str | None, bot_token: str) -> str:
    """Return the ``.../file/bot{token}`` prefix used for file downloads."""
    base = (api_base or _TELEGRAM_API_BASE).rstrip("/")
    if base.endswith(f"/bot{bot_token}"):
        root = base[: -len(f"/bot{bot_token}")]
    elif base.endswith("/bot"):
        root = base[: -len("/bot")]
    else:
        root = base
    return f"{root.rstrip('/')}/file/bot{bot_token}"


class TelegramApiError(Exception):
    """A non-ok Telegram Bot API response, preserving the real ``description``.

    Intentionally NOT a ``DomainError``: ``status_code`` here is Telegram's
    *outbound* response code, not a status to return to our API clients, and the
    error carries retry semantics (``retry_after``). It is only ever caught
    internally for delivery classification, never propagated to a controller, so
    it must not be auto-translated into an HTTP response.
    """

    def __init__(
        self,
        *,
        method: str,
        status_code: int,
        description: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.method = method
        self.status_code = status_code
        self.description = description
        self.retry_after = retry_after
        super().__init__(
            f"Telegram {method} failed (status {status_code}): "
            f"{description or 'no description'}"
        )

    @property
    def is_parse_entities_error(self) -> bool:
        """True when the failure is a Markdown/HTML entity parse error.

        Used to drive the safe-render fallback (retry the same text as plain
        text), since these are the failures escaping/chunking aims to avoid.
        """
        text = (self.description or "").lower()
        return "can't parse entities" in text or "can't find end" in text

    @property
    def is_message_too_long(self) -> bool:
        """True when Telegram refused the text for its length.

        Permanent as sent, but not as a reply: the same words split smaller
        are accepted, which is what the sender does with it.
        """
        return "too long" in (self.description or "").lower()

    @property
    def is_not_modified(self) -> bool:
        """True for editMessageText "message is not modified" — a benign no-op
        when the streamed progress text hasn't changed."""
        return "message is not modified" in (self.description or "").lower()


class TelegramClient:
    """Thin Telegram Bot API caller. One attempt per ``call``; retry via
    :func:`app.modules.agent_surfaces.platforms.delivery.with_retry`."""

    def __init__(
        self,
        *,
        bot_token: str,
        api_base: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._bot_token = bot_token
        self._api_base = api_base
        self._timeout = timeout

    @classmethod
    def from_credentials(
        cls,
        credentials: CredentialsLike,
        *,
        timeout: float = 10.0,
    ) -> "TelegramClient":
        """A client for this bot. The token is revealed here, into a client
        built for the call at hand."""
        telegram = TelegramCredentials.of(credentials)
        return cls(
            bot_token=reveal_secret(telegram.bot_token) or "",
            api_base=telegram.api_base_url or _TELEGRAM_API_BASE,
            timeout=timeout,
        )

    @property
    def base_url(self) -> str:
        return normalize_bot_base_url(self._api_base, self._bot_token)

    @property
    def file_base_url(self) -> str:
        return file_api_url(self._api_base, self._bot_token)

    async def call(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """POST a Bot API method, returning the parsed result or raising
        :class:`TelegramApiError` with the real ``description`` preserved."""
        # Guarded at the point of use: `api_base_url` is tenant-supplied and a
        # self-hosted Bot API server is a supported thing to point it at, so it
        # is checked rather than removed.
        await assert_safe_api_base(self.base_url, platform="Telegram")
        url = f"{self.base_url}/{method}"
        if client is not None:
            response = await client.post(url, json=payload)
        else:
            async with outbound_http(self._timeout) as own_client:
                response = await own_client.post(url, json=payload)
        return self._parse(method, response)

    async def call_multipart(
        self,
        method: str,
        *,
        fields: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST a Bot API method as multipart, for file uploads.

        ``timeout`` overrides the client's, because an upload is the one call
        whose duration scales with a payload of tens of megabytes; the default
        is sized for a JSON round-trip.
        """
        data = {
            key: json.dumps(value, separators=(",", ":"))
            if isinstance(value, (dict, list))
            else str(value)
            for key, value in fields.items()
        }
        await assert_safe_api_base(self.base_url, platform="Telegram")
        async with outbound_http(timeout or self._timeout) as client:
            response = await client.post(
                f"{self.base_url}/{method}",
                data=data,
                files=files,
            )
        return self._parse(method, response)

    def _parse(self, method: str, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            # httpx raises json.JSONDecodeError -- a ValueError -- for a body
            # that is not JSON. Anything else here is our bug, not theirs.
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.status_code >= 400 or not data.get("ok"):
            description = data.get("description")
            retry_after: float | None = None
            params = data.get("parameters")
            if isinstance(params, dict) and params.get("retry_after") is not None:
                try:
                    retry_after = float(params["retry_after"])
                except TypeError, ValueError:
                    retry_after = None
            raise TelegramApiError(
                method=method,
                status_code=response.status_code,
                description=description or (response.text or None),
                retry_after=retry_after,
            )
        return data


def classify_telegram_error(exc: Exception) -> DeliveryClassification:
    """Transient for 429 / 5xx / network errors; permanent for other 4xx."""
    if isinstance(exc, TelegramApiError):
        if exc.status_code == 429 or exc.status_code >= 500:
            return DeliveryClassification.TRANSIENT
        return DeliveryClassification.PERMANENT
    if isinstance(exc, httpx.RequestError):
        return DeliveryClassification.TRANSIENT
    return DeliveryClassification.PERMANENT


def classify_telegram_send_error(exc: Exception) -> DeliveryClassification:
    """``classify_telegram_error`` for a call that posts a message.

    The general classifier retries any transport failure, which is right for a
    read and wrong for a send: a read timeout after ``sendMessage`` went out may
    have posted it, and a retry posts it twice.
    """
    if isinstance(exc, TelegramApiError):
        return classify_telegram_error(exc)
    return classify_transport_failure(exc)


def telegram_retry_after(exc: Exception) -> float | None:
    if isinstance(exc, TelegramApiError) and exc.retry_after:
        return exc.retry_after
    return None
