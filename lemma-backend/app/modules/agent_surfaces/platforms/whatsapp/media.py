"""Moving files in and out of a WhatsApp chat.

Split from :mod:`service`, which owns credentials and the message verbs, because
media is a different exchange -- an upload, then a send that references it, then
a fallback -- and because it is where WhatsApp's per-type limits bite.
"""

from __future__ import annotations

import mimetypes
from typing import Any

import httpx

from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    filename_from_url,
    resolve_whatsapp_send_type,
    whatsapp_upload_mime,
)
from app.modules.agent_surfaces.platforms.whatsapp.text_format import to_plain_text

logger = get_logger(__name__)


async def download_attachment(
    client: WhatsAppClient, attachment: dict[str, Any]
) -> tuple[bytes, str, str] | None:
    """Download a single inbound WhatsApp attachment (no RunContext)."""
    if not client.has_credentials:
        return None
    media_id = str(attachment.get("id") or "").strip()
    if not media_id:
        return None
    media_info = await client.get_media_info(media_id)
    if not media_info:
        return None
    download_url = str(media_info.get("url") or "").strip()
    if not download_url:
        return None
    file_name = (
        str(attachment.get("name") or "").strip()
        or filename_from_url(download_url)
        or "whatsapp_file"
    )
    content = await client.download_media(download_url)
    mime_type = (
        str(attachment.get("mime_type") or media_info.get("mime_type") or "").strip()
        or mimetypes.guess_type(file_name)[0]
        or "application/octet-stream"
    )
    return content, file_name, mime_type


async def send_file(
    client: WhatsAppClient,
    *,
    phone_number_id: str,
    recipient_wa_id: str,
    file_name: str,
    file_bytes: bytes,
    mime_type: str,
    caption: str | None = None,
    recipient_type: str = "individual",
) -> bool:
    """Upload + send raw file bytes to a chat.

    Returns True on success; False when the upload is rejected so the caller
    falls back to a link. The kind is chosen before the upload
    (``resolve_whatsapp_send_type``): a format Meta does not play, or a file
    over its kind's ceiling, goes as a document from the start. A media type
    Meta still refuses at send time is retried once as a document.
    """
    send_type = resolve_whatsapp_send_type(
        delivery_mode="auto", mime_type=mime_type, size_bytes=len(file_bytes)
    )
    media_id = await _upload(
        client,
        phone_number_id=phone_number_id,
        file_name=file_name,
        file_bytes=file_bytes,
        mime_type=whatsapp_upload_mime(
            file_name=file_name, mime_type=mime_type, kind=send_type
        ),
    )
    if not media_id:
        return False
    kinds = [send_type] if send_type == "document" else [send_type, "document"]
    for kind in kinds:
        try:
            message_id = await client.send_media(
                phone_number_id=phone_number_id,
                to=recipient_wa_id,
                recipient_type=recipient_type,
                media_id=media_id,
                send_type=kind,
                file_name=file_name,
                caption=to_plain_text(caption) if caption else None,
            )
        except WhatsAppApiError as exc:
            if kind == kinds[-1] or exc.status_code >= 500 or exc.status_code == 429:
                raise
            logger.warning(
                "surface.whatsapp.media_type_rejected.degraded",
                mime_type=mime_type,
                send_type=kind,
                status_code=exc.status_code,
                exc_info=True,
            )
            continue
        return bool(message_id)
    return False


async def send_voice(
    client: WhatsAppClient,
    *,
    phone_number_id: str,
    recipient_wa_id: str,
    file_name: str,
    audio_bytes: bytes,
    mime_type: str,
    recipient_type: str = "individual",
) -> bool:
    """Send audio as a voice note: an OGG/Opus upload flagged ``voice``.

    False when it cannot be one -- not OGG, or the upload refused -- so the
    caller sends the same bytes as an ordinary file instead. The ``voice`` flag
    is what makes WhatsApp draw a voice-note bubble rather than an audio file;
    Meta documents it for OGG/Opus only, and a number or API version that
    refuses the flag is retried once without it, which still plays inline.
    """
    base_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if base_mime != "audio/ogg":
        return False
    media_id = await _upload(
        client,
        phone_number_id=phone_number_id,
        file_name=file_name,
        file_bytes=audio_bytes,
        mime_type="audio/ogg",
    )
    if not media_id:
        return False
    payload: dict[str, object] = {
        "messaging_product": "whatsapp",
        "recipient_type": recipient_type,
        "to": recipient_wa_id,
        "type": "audio",
        "audio": {"id": media_id, "voice": True},
    }
    try:
        message_id = await client.send_message_payload(
            phone_number_id=phone_number_id, payload=payload
        )
    except WhatsAppApiError as exc:
        if exc.status_code >= 500 or exc.status_code == 429:
            raise
        logger.warning(
            "surface.whatsapp.voice_flag_rejected.degraded",
            status_code=exc.status_code,
            meta_code=exc.meta_code,
            exc_info=True,
        )
        message_id = await client.send_message_payload(
            phone_number_id=phone_number_id,
            payload={**payload, "audio": {"id": media_id}},
        )
    return bool(message_id)


async def _upload(
    client: WhatsAppClient,
    *,
    phone_number_id: str,
    file_name: str,
    file_bytes: bytes,
    mime_type: str,
) -> str | None:
    """Upload bytes; None when Meta refuses them or never answers.

    Either way the caller has a better rung than raising -- a link, or the
    same bytes as a file -- so the failure is recorded here and not passed on.
    A transport failure counts: the client has already retried it.
    """
    try:
        return await client.upload_media(
            phone_number_id=phone_number_id,
            file_name=file_name,
            file_bytes=file_bytes,
            mime_type=mime_type,
        )
    except WhatsAppApiError as exc:
        # It carries the status code and the traceback, which `LOG_LEVEL=INFO`
        # would otherwise throw away.
        logger.warning(
            "surface.whatsapp.media_upload_rejected.degraded",
            mime_type=mime_type,
            status_code=exc.status_code,
            exc_info=True,
        )
    except httpx.HTTPError:
        logger.warning(
            "surface.whatsapp.media_upload_unreachable.degraded",
            mime_type=mime_type,
            exc_info=True,
        )
    return None
