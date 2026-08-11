from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.entities import ConversationType
from app.modules.agent_surfaces.platforms.email_common import (
    parse_email_identity,
    plain_text_from_html,
    strip_quoted_reply,
)


def _header_map(headers: Any) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if not isinstance(headers, list):
        return normalized
    for item in headers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        if name and value:
            normalized[name] = value
    return normalized


def _first_recipient(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value


def _body_text(data: dict[str, Any]) -> str:
    body = (
        data.get("text_body")
        or data.get("body_text")
        or data.get("body")
        or data.get("snippet")
        or ""
    )
    if isinstance(body, dict):
        content = str(body.get("content") or body.get("text") or "")
        content_type = str(body.get("contentType") or body.get("content_type") or "")
        if content_type.lower() == "html":
            return plain_text_from_html(content)
        return content
    return str(body)


def _normalize_attachment(
    raw: dict[str, Any],
    *,
    message_id: str | None,
) -> dict[str, Any] | None:
    attachment_id = raw.get("attachment_id") or raw.get("id")
    name = raw.get("name") or raw.get("filename") or raw.get("file_name")
    mime_type = (
        raw.get("contentType")
        or raw.get("mime_type")
        or raw.get("content_type")
    )
    content_bytes = (
        raw.get("contentBytes")
        or raw.get("content_bytes")
        or raw.get("content_bytes_base64")
    )
    if not any([attachment_id, name, content_bytes]):
        return None
    return {
        "id": str(attachment_id).strip() or None if attachment_id is not None else None,
        "name": str(name).strip() or None if name is not None else None,
        "mime_type": str(mime_type).strip() or None if mime_type is not None else None,
        "content_type": str(mime_type or "").strip(),
        "size": raw.get("size"),
        "message_id": message_id,
        "content_bytes_base64": (
            str(content_bytes).strip() or None if content_bytes is not None else None
        ),
        "is_inline": bool(raw.get("isInline") or raw.get("is_inline")),
        "content_id": str(raw.get("contentId") or raw.get("content_id") or "").strip() or None,
        "odata_type": str(raw.get("@odata.type") or raw.get("odata_type") or "").strip()
        or None,
    }


class OutlookMessageParser:
    def parse(self, payload: dict[str, Any]) -> ParsedInboundSurfaceEvent | None:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        headers = _header_map(data.get("internetMessageHeaders"))
        thread_id = str(
            data.get("thread_id")
            or data.get("conversation_id")
            or data.get("conversationId")
            or data.get("id")
            or ""
        ).strip()
        provider_message_id = str(
            data.get("message_id")
            or data.get("messageId")
            or data.get("id")
            or ""
        ).strip()
        internet_message_id = str(
            data.get("internet_message_id")
            or data.get("internetMessageId")
            or ""
        ).strip()
        external_message_id = internet_message_id or provider_message_id
        sender_identity = parse_email_identity(
            data.get("sender") or data.get("from"),
            fallback_email=data.get("sender_email") or data.get("from_email"),
            fallback_name=data.get("sender_name"),
        )
        mailbox_identity = parse_email_identity(
            data.get("mailbox")
            or data.get("to")
            or _first_recipient(data.get("toRecipients"))
            or _first_recipient(data.get("to_recipients")),
            fallback_email=(
                data.get("mailbox_email")
                or data.get("to_email")
                or data.get("userPrincipalName")
            ),
        )
        reply_to_identity = parse_email_identity(
            _first_recipient(data.get("replyTo")) or _first_recipient(data.get("reply_to")),
            fallback_email=sender_identity.email,
            fallback_name=sender_identity.display_name,
        )

        if thread_id and external_message_id and sender_identity.email:
            subject = str(data.get("subject") or "").strip()
            # Quoted original trimmed for the same reason as every other
            # provider: a reply should be what the person just wrote.
            body = strip_quoted_reply(_body_text(data).strip(), subject)
            message_text = f"Email subject: {subject}\n\n{body}".strip()
            header_references = [
                ref.strip()
                for ref in str(headers.get("references") or "").split()
                if ref.strip()
            ]
            references = [
                str(ref)
                for ref in list(data.get("references") or header_references)
                if ref
            ]
            in_reply_to = str(
                data.get("in_reply_to")
                or headers.get("in-reply-to")
                or internet_message_id
                or provider_message_id
            ).strip() or None

            attachments = [
                normalized
                for item in list(data.get("attachments") or [])
                if isinstance(item, dict)
                for normalized in [
                    _normalize_attachment(item, message_id=provider_message_id or None)
                ]
                if normalized is not None
            ]

            return ParsedInboundSurfaceEvent(
                platform="OUTLOOK",
                conversation_type=ConversationType.EXTERNAL_DM,
                external_channel_id=mailbox_identity.email,
                external_thread_id=thread_id,
                external_message_id=external_message_id,
                sender_external_user_id=sender_identity.email,
                sender_email=sender_identity.email,
                sender_display_name=sender_identity.display_name,
                message_text=message_text,
                is_dm=True,
                mentioned_agent=True,
                should_start_conversation=True,
                reply_target={
                    "recipient_email": reply_to_identity.email or sender_identity.email,
                    "subject": subject,
                    "thread_id": thread_id,
                    "message_id": provider_message_id,
                    "internet_message_id": internet_message_id or None,
                    "references": references,
                    "in_reply_to": in_reply_to,
                    "mailbox_email": mailbox_identity.email,
                },
                metadata={
                    "channel": "email",
                    "mailbox_email": mailbox_identity.email,
                    "subject": subject,
                    "thread_id": thread_id,
                    "message_id": provider_message_id or None,
                    "internet_message_id": internet_message_id or None,
                    "reply_to_email": reply_to_identity.email or sender_identity.email,
                    "references": references,
                    "in_reply_to": in_reply_to,
                    "attachments": attachments,
                },
                raw_payload=payload,
            )

        # Outlook trigger webhooks can arrive as sparse envelopes that only include the
        # provider message ID. We create a typed placeholder event and let the adapter
        # enrich it from Microsoft Graph before identity resolution and conversation routing.
        if provider_message_id:
            return ParsedInboundSurfaceEvent(
                platform="OUTLOOK",
                conversation_type=ConversationType.EXTERNAL_DM,
                external_channel_id=None,
                external_thread_id=thread_id or provider_message_id,
                external_message_id=external_message_id or provider_message_id,
                sender_external_user_id=None,
                sender_email=None,
                sender_display_name=None,
                message_text="",
                is_dm=True,
                mentioned_agent=True,
                should_start_conversation=True,
                reply_target={
                    "message_id": provider_message_id,
                    "internet_message_id": internet_message_id or None,
                },
                metadata={
                    "channel": "email",
                    "message_id": provider_message_id,
                    "internet_message_id": internet_message_id or None,
                    "event_type": str(data.get("event_type") or "").strip() or None,
                    "requires_message_fetch": True,
                },
                raw_payload=payload,
            )

        return None
