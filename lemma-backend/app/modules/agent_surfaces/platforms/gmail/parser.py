from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import ConversationType, ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.platforms.email_common import (
    decode_base64_bytes,
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


def _walk_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parts = payload.get("parts")
    if not isinstance(parts, list):
        return []

    flattened: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        flattened.append(part)
        flattened.extend(_walk_parts(part))
    return flattened


def _decode_gmail_body(data: Any, *, content_type: str) -> str:
    if not isinstance(data, str) or not data.strip():
        return ""
    try:
        decoded = decode_base64_bytes(data, urlsafe=True).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""
    if content_type.lower().startswith("text/html"):
        return plain_text_from_html(decoded)
    return decoded


def _read_email_body(data: dict[str, Any]) -> str:
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


def _read_gmail_payload_body(data: dict[str, Any]) -> str:
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return ""

    candidates = [payload, *_walk_parts(payload)]

    for part in candidates:
        mime_type = str(part.get("mimeType") or part.get("mime_type") or "").strip()
        if not mime_type.startswith("text/plain"):
            continue
        body = part.get("body")
        if not isinstance(body, dict):
            continue
        decoded = _decode_gmail_body(body.get("data"), content_type=mime_type).strip()
        if decoded:
            return decoded

    for part in candidates:
        mime_type = str(part.get("mimeType") or part.get("mime_type") or "").strip()
        if not mime_type.startswith("text/html"):
            continue
        body = part.get("body")
        if not isinstance(body, dict):
            continue
        decoded = _decode_gmail_body(body.get("data"), content_type=mime_type).strip()
        if decoded:
            return decoded

    return ""


def _normalize_attachment(
    raw: dict[str, Any],
    *,
    message_id: str,
) -> dict[str, Any] | None:
    body = raw.get("body")
    body_data = body if isinstance(body, dict) else {}
    attachment_id = (
        raw.get("attachment_id")
        or raw.get("attachmentId")
        or raw.get("id")
        or body_data.get("attachmentId")
    )
    name = raw.get("name") or raw.get("filename") or raw.get("file_name")
    mime_type = (
        raw.get("mime_type")
        or raw.get("mimeType")
        or raw.get("content_type")
        or raw.get("contentType")
    )
    size = raw.get("size") or body_data.get("size")
    content_bytes_base64 = (
        raw.get("content_bytes_base64")
        or raw.get("data")
        or body_data.get("data")
    )
    if not any([attachment_id, name, content_bytes_base64]):
        return None
    return {
        "id": (
            str(attachment_id).strip() or None
            if attachment_id is not None
            else None
        ),
        "name": str(name).strip() or None if name is not None else None,
        "mime_type": str(mime_type).strip() or None if mime_type is not None else None,
        "content_type": str(mime_type or "").strip(),
        "size": int(size) if isinstance(size, int) else size,
        "message_id": message_id,
        "content_bytes_base64": (
            str(content_bytes_base64).strip() or None
            if content_bytes_base64 is not None
            else None
        ),
    }


def _dedupe_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str | None, str | None, str | None]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = (
            item.get("id"),
            item.get("name"),
            item.get("content_type"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _extract_message_headers(data: dict[str, Any]) -> dict[str, str]:
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return {}
    return _header_map(payload.get("headers"))


class GmailMessageParser:
    def parse(self, payload: dict[str, Any]) -> ParsedInboundSurfaceEvent | None:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        headers = _extract_message_headers(data)
        thread_id = str(
            data.get("thread_id")
            or data.get("threadId")
            or headers.get("thread-id")
            or data.get("conversation_id")
            or (data.get("payload") or {}).get("threadId")
            or data.get("id")
            or ""
        ).strip()
        message_id = str(
            data.get("message_id")
            or data.get("messageId")
            or data.get("id")
            or ""
        ).strip()
        sender_identity = parse_email_identity(
            data.get("sender") or data.get("from") or headers.get("from"),
            fallback_email=data.get("sender_email") or data.get("from_email"),
            fallback_name=data.get("sender_name"),
        )
        mailbox_identity = parse_email_identity(
            data.get("mailbox")
            or data.get("to")
            or headers.get("delivered-to")
            or headers.get("to"),
            fallback_email=data.get("mailbox_email") or data.get("to_email"),
        )
        if not thread_id or not message_id or not sender_identity.email:
            return None

        reply_identity = parse_email_identity(
            headers.get("reply-to"),
            fallback_email=sender_identity.email,
            fallback_name=sender_identity.display_name,
        )

        subject = str(data.get("subject") or headers.get("subject") or "").strip()
        body = (
            str(data.get("message_text") or "").strip()
            or _read_email_body(data).strip()
            or _read_gmail_payload_body(data).strip()
            or str(((data.get("preview") or {}).get("body")) or "").strip()
        )
        # Drop the quoted original. Without this every reply carries the whole
        # thread forward, so by the fourth exchange most of the prompt is the
        # agent re-reading its own earlier messages.
        message_text = f"Email subject: {subject}\n\n{strip_quoted_reply(body, subject)}".strip()

        attachment_candidates = [
            normalized
            for collection in (
                list(data.get("attachments") or []),
                list(data.get("attachment_list") or []),
                _walk_parts(data.get("payload") if isinstance(data.get("payload"), dict) else {}),
            )
            for item in collection
            if isinstance(item, dict)
            for normalized in [_normalize_attachment(item, message_id=message_id)]
            if normalized is not None
        ]
        attachments = _dedupe_attachments(attachment_candidates)

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
        internet_message_id = str(headers.get("message-id") or "").strip() or None
        in_reply_to = (
            str(data.get("in_reply_to") or headers.get("in-reply-to") or "").strip()
            or internet_message_id
        )

        return ParsedInboundSurfaceEvent(
            platform="GMAIL",
            conversation_type=ConversationType.EXTERNAL_DM,
            external_channel_id=mailbox_identity.email,
            external_thread_id=thread_id,
            external_message_id=message_id,
            sender_external_user_id=sender_identity.email,
            sender_email=sender_identity.email,
            sender_display_name=sender_identity.display_name,
            message_text=message_text,
            is_dm=True,
            mentioned_agent=True,
            should_start_conversation=True,
            reply_target={
                "recipient_email": reply_identity.email or sender_identity.email,
                "subject": subject,
                "thread_id": thread_id,
                "message_id": message_id,
                "references": references,
                "in_reply_to": in_reply_to,
                "mailbox_email": mailbox_identity.email,
            },
            metadata={
                "channel": "email",
                "mailbox_email": mailbox_identity.email,
                "subject": subject,
                "thread_id": thread_id,
                "message_id": message_id,
                "internet_message_id": internet_message_id,
                "reply_to_email": reply_identity.email or sender_identity.email,
                "references": references,
                "in_reply_to": in_reply_to,
                "attachments": attachments,
            },
            raw_payload=payload,
        )
