"""Unified WhatsApp Cloud API (Graph) transport for agent surfaces.

Single home for base-URL resolution, the ``{phone_number_id}/messages`` /
``{phone_number_id}/media`` path shapes, bearer auth, multipart media upload, and
error-envelope parsing. Adopted by :class:`WhatsAppPlatformService` so the
outbound send/interactive/typing/media paths no longer keep divergent inline
``httpx`` calls.

Library evaluation (pywa): NOT adopted. ``pywa`` is a pleasant single-tenant
client, but agent surfaces are multi-tenant — every call carries a per-bot
``phone_number_id`` + ``access_token`` resolved from the pod's connector
account, so a per-instance library client is the wrong shape and would add a
heavy dependency for payloads this thin client already covers (text, interactive
buttons/list/cta_url, mark-read + typing, media upload/send/download). The typed
payload shapes are borrowed from pywa's public API but implemented in-package.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.modules.agent_surfaces.platforms.common import assert_safe_api_base
from app.modules.agent_surfaces.platforms.delivery import (
    DeliveryClassification,
    RetryPolicy,
    with_retry,
)
from app.core.net.capped_read import read_capped
from app.modules.agent_surfaces.platforms.attachment_limits import (
    INBOUND_ATTACHMENT_BYTE_CAP,
)

# The one canonical WhatsApp Graph API base. ``api_base_url`` in the bot
# credentials overrides it (used by tests to point at a fake server).
_WHATSAPP_API_BASE = "https://graph.facebook.com/v21.0"

#: The Graph version the Groups API was published under. Ordinary messaging
#: stays on the base above; everything that names a group -- creating one,
#: its invite link, a message to it -- goes through this version instead.
_GROUPS_API_VERSION = "v23.0"

#: Meta's limits on a group's name and description.
GROUP_SUBJECT_MAX_CHARS = 128
GROUP_DESCRIPTION_MAX_CHARS = 2048


def groups_api_base(api_base: str) -> str:
    """The same Graph base at the Groups API's version.

    A base that names no version (a test server, a proxy) is left alone: there
    is nothing to change and nothing it would understand if changed.
    """
    trimmed = api_base.rstrip("/")
    head, _, tail = trimmed.rpartition("/")
    if head and re.fullmatch(r"v\d+\.\d+", tail):
        return f"{head}/{_GROUPS_API_VERSION}"
    return trimmed


def resolve_api_base(credentials: dict[str, Any] | None) -> str:
    """Resolve the WhatsApp Graph API base, honoring a credential override."""
    if credentials:
        candidate = str(credentials.get("api_base_url") or "").strip()
        if candidate:
            return candidate
    return _WHATSAPP_API_BASE


class WhatsAppApiError(Exception):
    """A non-2xx WhatsApp Graph API response, preserving a body excerpt.

    Intentionally NOT a ``DomainError``: ``status_code`` here is Meta's *outbound*
    response code, not a status to return to our API clients. It is only ever
    caught internally (delivery best-effort / media-fallback), never propagated to
    a controller, so it must not be auto-translated into an HTTP response.
    """

    def __init__(
        self,
        *,
        method: str,
        status_code: int,
        body_excerpt: str | None = None,
    ) -> None:
        self.method = method
        self.status_code = status_code
        self.body_excerpt = body_excerpt
        super().__init__(
            f"WhatsApp {method} failed (status {status_code}): "
            f"{body_excerpt or 'no body'}"
        )

    @property
    def meta_code(self) -> int | None:
        """Meta's own error code (``error.code``), when the body carries one.

        Read from the excerpt, which may be cut short, so the number is found
        by its key rather than by parsing the whole envelope.
        """
        found = _META_CODE.search(self.body_excerpt or "")
        return int(found.group(1)) if found else None


#: ``"code": 131215`` -- the first ``code`` in Meta's envelope is ``error.code``.
_META_CODE = re.compile(r'"code"\s*:\s*(\d+)')


class WhatsAppClient:
    """Thin WhatsApp Cloud API caller.

    Sends retry with bounded backoff through the same ``with_retry`` Telegram
    and Slack use, but a message and an upload retry on different failures --
    see ``classify_whatsapp_error``: a message sent twice is a duplicate on the
    person's phone, an upload made twice is an orphaned media id nobody sees.
    """

    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str = "",
        api_base: str | None = None,
        timeout: float = 60.0,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._api_base = (api_base or _WHATSAPP_API_BASE).rstrip("/")
        self._timeout = timeout
        self._retry_policy = retry_policy or RetryPolicy()

    @classmethod
    def from_credentials(
        cls, credentials: dict[str, Any], *, timeout: float = 60.0
    ) -> "WhatsAppClient":
        return cls(
            access_token=str(credentials.get("access_token") or ""),
            phone_number_id=str(credentials.get("phone_number_id") or ""),
            api_base=resolve_api_base(credentials),
            timeout=timeout,
        )

    @property
    def api_base(self) -> str:
        return self._api_base

    @property
    def has_credentials(self) -> bool:
        return bool(self._access_token)

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    # ---- typed methods -----------------------------------------------------

    async def send_text(
        self,
        *,
        phone_number_id: str,
        to: str,
        body: str,
        preview_url: bool = False,
        reply_to_message_id: str | None = None,
    ) -> str | None:
        """Send a plain-text message; return the outbound message id if any."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": body, "preview_url": preview_url},
        }
        if reply_to_message_id:
            payload["context"] = {"message_id": reply_to_message_id}
        return await self.send_message_payload(
            phone_number_id=phone_number_id, payload=payload
        )

    async def send_interactive(
        self,
        *,
        phone_number_id: str,
        to: str,
        interactive: dict[str, Any],
    ) -> str | None:
        """Send an interactive message (buttons / list / cta_url)."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        return await self.send_message_payload(
            phone_number_id=phone_number_id, payload=payload
        )

    async def mark_read_and_typing(
        self,
        *,
        phone_number_id: str,
        message_id: str,
    ) -> None:
        """Mark an inbound message read (blue ticks) and show a typing bubble.

        WhatsApp couples the read receipt and the typing indicator into one call.
        The typing bubble shows for ~25s or until the next message is sent — so a
        single call at run start is enough; there is no refresh API.
        """
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
        await self.send_message_payload(
            phone_number_id=phone_number_id, payload=payload
        )

    async def react(
        self,
        *,
        phone_number_id: str,
        to: str,
        message_id: str,
        emoji: str,
    ) -> None:
        """Post an emoji reaction to an inbound message (indicator fallback)."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "reaction",
            "reaction": {"message_id": message_id, "emoji": emoji},
        }
        await self.send_message_payload(
            phone_number_id=phone_number_id, payload=payload
        )

    async def send_media(
        self,
        *,
        phone_number_id: str,
        to: str,
        media_id: str,
        send_type: str,
        file_name: str,
        caption: str | None = None,
        recipient_type: str = "individual",
    ) -> str | None:
        """Send a previously uploaded media object to a recipient."""
        media_payload: dict[str, Any] = {"id": media_id}
        if send_type == "document":
            media_payload["filename"] = file_name
        if caption and send_type in {"document", "image", "video"}:
            media_payload["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": recipient_type,
            "to": to,
            "type": send_type,
            send_type: media_payload,
        }
        return await self.send_message_payload(
            phone_number_id=phone_number_id, payload=payload
        )

    async def send_message_payload(
        self,
        *,
        phone_number_id: str,
        payload: dict[str, Any],
    ) -> str | None:
        """POST a fully-formed ``/messages`` payload; return first message id.

        Retried on transient failures, except for the two payloads that are
        indicators rather than messages: a keep-alive that fails is replaced by
        the next one, and a retry there would only delay the caller waiting on
        a typing bubble.
        """
        base = (
            groups_api_base(self._api_base)
            if payload.get("recipient_type") == "group"
            else self._api_base
        )
        url = f"{base}/{phone_number_id}/messages"
        is_indicator = payload.get("status") == "read" or payload.get("type") == (
            "reaction"
        )
        if not is_indicator:
            data = await with_retry(
                lambda: self._post_json(url, json=payload, method="messages"),
                policy=self._retry_policy,
                classify=classify_whatsapp_error,
                retry_after=whatsapp_retry_after,
            )
        else:
            data = await self._post_json(url, json=payload, method="messages")
        messages = (data or {}).get("messages") or []
        first = messages[0] if messages else {}
        if not isinstance(first, dict):
            return None
        return str(first.get("id") or "").strip() or None

    async def upload_media(
        self,
        *,
        phone_number_id: str,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
    ) -> str | None:
        """Upload a media object; return its media id."""
        url = f"{self._api_base}/{phone_number_id}/media"
        await assert_safe_api_base(url, platform="WhatsApp")

        async def upload() -> dict[str, object]:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    data={"messaging_product": "whatsapp", "type": mime_type},
                    files={"file": (file_name, file_bytes, mime_type)},
                    headers=self._auth_headers,
                )
            return self._parse(response, method="media.upload")

        # An upload nobody references is invisible, so unlike a message it is
        # safe to repeat on any transient failure, a read timeout included.
        data = await with_retry(
            upload,
            policy=self._retry_policy,
            classify=classify_whatsapp_idempotent_error,
            retry_after=whatsapp_retry_after,
        )
        return str((data or {}).get("id") or "").strip() or None

    async def get_media_info(self, media_id: str) -> dict[str, Any] | None:
        url = f"{self._api_base}/{media_id}"
        await assert_safe_api_base(url, platform="WhatsApp")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._auth_headers)
        data = self._parse(response, method="media.info")
        return data if isinstance(data, dict) else None

    async def download_media(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "GET", url, headers=self._auth_headers
            ) as response:
                if response.status_code >= 400:
                    # The excerpt helper needs a body, and an error body is
                    # small by construction -- read it before giving up.
                    await response.aread()
                    raise WhatsAppApiError(
                        method="media.download",
                        status_code=response.status_code,
                        body_excerpt=_body_excerpt(response),
                    )
                return await read_capped(
                    response.aiter_bytes(), max_bytes=INBOUND_ATTACHMENT_BYTE_CAP
                )

    async def create_group(
        self, *, phone_number_id: str, subject: str, description: str | None = None
    ) -> str | None:
        """Ask Meta to create a group; return the request id it will confirm.

        Creation is asynchronous: the group's id arrives later, in a
        ``group_lifecycle_update`` webhook that echoes this request id. Meta's
        spec calls the field ``request_id``; a proxy (360dialog) has been seen
        answering ``id`` instead, so both are read.
        """
        payload: dict[str, object] = {
            "messaging_product": "whatsapp",
            "subject": subject[:GROUP_SUBJECT_MAX_CHARS],
        }
        if description:
            payload["description"] = description[:GROUP_DESCRIPTION_MAX_CHARS]
        data = await self._post_json(
            f"{groups_api_base(self._api_base)}/{phone_number_id}/groups",
            json=payload,
            method="groups.create",
        )
        return str(data.get("request_id") or data.get("id") or "").strip() or None

    async def get_invite_link(self, group_id: str) -> str | None:
        """The link people join a group by."""
        url = f"{groups_api_base(self._api_base)}/{group_id}/invite_link"
        await assert_safe_api_base(url, platform="WhatsApp")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._auth_headers)
        data = self._parse(response, method="groups.invite_link")
        return str(data.get("invite_link") or "").strip() or None

    async def get_phone_number_field(self, field: str) -> str | None:
        """Read one field off the phone-number node (e.g. display_phone_number)."""
        if not self._access_token or not self._phone_number_id:
            return None
        url = f"{self._api_base}/{self._phone_number_id}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                url, params={"fields": field}, headers=self._auth_headers
            )
        data = self._parse(response, method="phone_number.get")
        return str((data or {}).get(field) or "").strip() or None

    # ---- transport ---------------------------------------------------------

    async def _post_json(
        self, url: str, *, json: dict[str, Any], method: str
    ) -> dict[str, Any]:
        # `api_base_url` is tenant-supplied (a sovereign Graph endpoint is a
        # real deployment), so the target is checked before the token goes out.
        await assert_safe_api_base(url, platform="WhatsApp")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=json, headers=self._auth_headers)
        return self._parse(response, method=method)

    def _parse(self, response: httpx.Response, *, method: str) -> dict[str, Any]:
        if response.status_code >= 400:
            raise WhatsAppApiError(
                method=method,
                status_code=response.status_code,
                body_excerpt=_body_excerpt(response),
            )
        try:
            data = response.json()
        except ValueError:
            # httpx raises json.JSONDecodeError -- a ValueError -- for a body
            # that is not JSON. Anything else here is our bug, not theirs.
            data = {}
        return data if isinstance(data, dict) else {}


#: Meta's throttling codes. Each one is a refusal *before* anything was sent --
#: account and app rate limits (4, 80007, 130429), the spam limit (131048) and
#: the per-recipient pair limit (131056) -- so retrying cannot duplicate. They
#: arrive as an HTTP 400, which read as permanent and lost the message.
RETRYABLE_META_CODES = frozenset({4, 80007, 130429, 131048, 131056})

#: Meta asks for a pause before the next message to the same person without
#: saying how long. Six seconds is a deliberate guess, inside the retry
#: policy's cap, and the ordinary backoff would retry well before it.
_PAIR_RATE_LIMIT_CODE = 131056
_PAIR_RATE_LIMIT_PAUSE_SECONDS = 6.0

#: Failures where the request provably never reached Meta: no connection, or
#: no free connection to use. Anything later -- a read timeout, a dropped
#: response -- may have been a message Meta accepted and delivered.
_NOT_SENT_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


def classify_whatsapp_error(exc: Exception) -> DeliveryClassification:
    """Whether a *message* send may be tried again without a duplicate.

    Only when Meta did not take it: the connection never opened, a 429 or a
    503 (refused, not processed), or one of Meta's throttling codes. Any other
    5xx and a read timeout are left alone -- Meta may have delivered the
    message before failing to say so, and the retry then reached the person's
    phone twice.
    """
    if isinstance(exc, WhatsAppApiError):
        if exc.status_code in (429, 503) or exc.meta_code in RETRYABLE_META_CODES:
            return DeliveryClassification.TRANSIENT
        return DeliveryClassification.PERMANENT
    if isinstance(exc, _NOT_SENT_ERRORS):
        return DeliveryClassification.TRANSIENT
    return DeliveryClassification.PERMANENT


def classify_whatsapp_idempotent_error(exc: Exception) -> DeliveryClassification:
    """For calls that are safe to repeat (an upload): any transient failure."""
    if isinstance(exc, WhatsAppApiError):
        if (
            exc.status_code == 429
            or exc.status_code >= 500
            or exc.meta_code in RETRYABLE_META_CODES
        ):
            return DeliveryClassification.TRANSIENT
        return DeliveryClassification.PERMANENT
    if isinstance(exc, httpx.RequestError):
        return DeliveryClassification.TRANSIENT
    return DeliveryClassification.PERMANENT


def whatsapp_retry_after(exc: Exception) -> float | None:
    """The pause Meta's pair rate limit wants; exponential backoff otherwise."""
    if isinstance(exc, WhatsAppApiError) and exc.meta_code == _PAIR_RATE_LIMIT_CODE:
        return _PAIR_RATE_LIMIT_PAUSE_SECONDS
    return None


def _body_excerpt(response: httpx.Response, *, limit: int = 500) -> str:
    try:
        body = str(response.text or "").strip()
    except Exception:
        body = ""
    return body[:limit] + "..." if len(body) > limit else body
