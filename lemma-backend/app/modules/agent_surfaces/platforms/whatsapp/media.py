"""Moving files in and out of a WhatsApp chat.

Split from :mod:`service`, which owns credentials and the message verbs, because
media is a different exchange -- an upload, then a send that references it, then
a fallback -- and because it is where WhatsApp's per-type limits bite.
"""

from __future__ import annotations

import mimetypes
from typing import Any

from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    filename_from_url,
    resolve_whatsapp_send_type,
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
) -> bool:
    """Upload + send raw file bytes to a chat.

    Returns True on success; False when the upload is rejected so the caller
    falls back to a link. A media type Meta refuses at send time is retried once
    as a document first: it accepts far fewer image, audio and video formats than
    documents (an image is JPEG or PNG and nothing else) and says so only when
    the message is sent, so a rejected type is a reason to send the same bytes as
    a document rather than a reason to lose them.
    """
    send_type = resolve_whatsapp_send_type(delivery_mode="auto", mime_type=mime_type)
    try:
        media_id = await client.upload_media(
            phone_number_id=phone_number_id,
            file_name=file_name,
            file_bytes=file_bytes,
            mime_type=mime_type,
        )
    except WhatsAppApiError as exc:
        # Unsupported media / rejected upload — caller falls back to a link, so
        # the person still gets the file. It carries the status code and the
        # traceback, which `LOG_LEVEL=INFO` would otherwise throw away.
        logger.warning(
            "surface.whatsapp.media_upload_rejected.degraded",
            mime_type=mime_type,
            status_code=exc.status_code,
            exc_info=True,
        )
        return False
    if not media_id:
        return False
    kinds = [send_type] if send_type == "document" else [send_type, "document"]
    for kind in kinds:
        try:
            message_id = await client.send_media(
                phone_number_id=phone_number_id,
                to=recipient_wa_id,
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
