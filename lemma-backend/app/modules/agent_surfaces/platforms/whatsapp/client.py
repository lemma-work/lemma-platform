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

from typing import Any

import httpx

from app.modules.agent_surfaces.platforms.delivery import DeliveryClassification

# The one canonical WhatsApp Graph API base. ``api_base_url`` in the bot
# credentials overrides it (used by tests to point at a fake server).
_WHATSAPP_API_BASE = "https://graph.facebook.com/v21.0"


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


class WhatsAppClient:
    """Thin WhatsApp Cloud API caller. One attempt per method; best-effort retry
    is the caller's concern (delivery is never allowed to fail a run)."""

    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str = "",
        api_base: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._api_base = (api_base or _WHATSAPP_API_BASE).rstrip("/")
        self._timeout = timeout

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
    ) -> str | None:
        """Send a plain-text message; return the outbound message id if any."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": body, "preview_url": preview_url},
        }
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
    ) -> str | None:
        """Send a previously uploaded media object to a recipient."""
        media_payload: dict[str, Any] = {"id": media_id}
        if send_type == "document":
            media_payload["filename"] = file_name
        if caption and send_type in {"document", "image", "video"}:
            media_payload["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
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
        """POST a fully-formed ``/messages`` payload; return first message id."""
        data = await self._post_json(
            f"{self._api_base}/{phone_number_id}/messages",
            json=payload,
            method="messages",
        )
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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (file_name, file_bytes, mime_type)},
                headers=self._auth_headers,
            )
        data = self._parse(response, method="media.upload")
        return str((data or {}).get("id") or "").strip() or None

    async def get_media_info(self, media_id: str) -> dict[str, Any] | None:
        url = f"{self._api_base}/{media_id}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._auth_headers)
        data = self._parse(response, method="media.info")
        return data if isinstance(data, dict) else None

    async def download_media(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._auth_headers)
            if response.status_code >= 400:
                raise WhatsAppApiError(
                    method="media.download",
                    status_code=response.status_code,
                    body_excerpt=_body_excerpt(response),
                )
            return response.content

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
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}


def classify_whatsapp_error(exc: Exception) -> DeliveryClassification:
    """Transient for 429 / 5xx / network errors; permanent for other 4xx."""
    if isinstance(exc, WhatsAppApiError):
        if exc.status_code == 429 or exc.status_code >= 500:
            return DeliveryClassification.TRANSIENT
        return DeliveryClassification.PERMANENT
    if isinstance(exc, httpx.RequestError):
        return DeliveryClassification.TRANSIENT
    return DeliveryClassification.PERMANENT


def _body_excerpt(response: httpx.Response, *, limit: int = 500) -> str:
    try:
        body = str(response.text or "").strip()
    except Exception:
        body = ""
    return body[:limit] + "..." if len(body) > limit else body
