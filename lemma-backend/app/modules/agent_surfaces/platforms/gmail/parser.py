from __future__ import annotations

from dataclasses import dataclass

from app.modules.agent_surfaces.platforms.common import (
    payload_any,
    payload_first,
    payload_section,
    payload_text,
)

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.email_common import (
    ParsedEmailIdentity,
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
        name = payload_text(item, "name").strip().lower()
        value = payload_text(item, "value").strip()
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
        payload_any(data, "text_body", "body_text", "body") or data.get("snippet") or ""
    )
    if isinstance(body, dict):
        content = payload_first(body, "content", "text")
        content_type = payload_first(body, "contentType", "content_type")
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
        mime_type = payload_first(part, "mimeType", "mime_type").strip()
        if not mime_type.startswith("text/plain"):
            continue
        body = part.get("body")
        if not isinstance(body, dict):
            continue
        decoded = _decode_gmail_body(body.get("data"), content_type=mime_type).strip()
        if decoded:
            return decoded

    for part in candidates:
        mime_type = payload_first(part, "mimeType", "mime_type").strip()
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
    attachment_id = payload_any(
        raw, "attachment_id", "attachmentId", "id"
    ) or body_data.get("attachmentId")
    name = payload_any(raw, "name", "filename", "file_name")
    mime_type = payload_any(raw, "mime_type", "mimeType", "content_type", "contentType")
    size = raw.get("size") or body_data.get("size")
    content_bytes_base64 = payload_any(
        raw, "content_bytes_base64", "data"
    ) or body_data.get("data")
    if not any([attachment_id, name, content_bytes_base64]):
        return None
    return {
        "id": (
            str(attachment_id).strip() or None if attachment_id is not None else None
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


@dataclass(frozen=True)
class _GmailEnvelope:
    """A push notification's addressing, before its content is read.

    `is_complete` is the guard the parser used to spell inline: without a
    thread, a message id and a sender we cannot route it, so there is nothing
    to be gained by reading the body.
    """

    data: dict[str, Any]
    headers: dict[str, str]
    thread_id: str
    message_id: str
    sender_identity: ParsedEmailIdentity
    mailbox_identity: ParsedEmailIdentity

    @property
    def is_complete(self) -> bool:
        return bool(self.thread_id and self.message_id and self.sender_identity.email)


class GmailMessageParser:
    def parse(self, payload: dict[str, Any]) -> ParsedInboundSurfaceEvent | None:
        """A Gmail push, once it has an addressee, a thread and a message."""
        envelope = self._envelope(payload)
        if not envelope.is_complete:
            return None
        return self._message_event(envelope, payload)

    def _envelope(self, payload: dict[str, Any]) -> _GmailEnvelope:
        """Who sent it, to which mailbox, in which thread."""
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        headers = _extract_message_headers(data)
        thread_id = str(
            payload_any(data, "thread_id", "threadId")
            or headers.get("thread-id")
            or data.get("conversation_id")
            or (payload_section(data, "payload")).get("threadId")
            or data.get("id")
            or ""
        ).strip()
        message_id = payload_first(data, "message_id", "messageId", "id").strip()
        sender_identity = parse_email_identity(
            payload_any(data, "sender", "from") or headers.get("from"),
            fallback_email=payload_any(data, "sender_email", "from_email"),
            fallback_name=data.get("sender_name"),
        )
        mailbox_identity = parse_email_identity(
            payload_any(data, "mailbox", "to")
            or payload_any(headers, "delivered-to", "to"),
            fallback_email=payload_any(data, "mailbox_email", "to_email"),
        )
        return _GmailEnvelope(
            data=data,
            headers=headers,
            thread_id=thread_id,
            message_id=message_id,
            sender_identity=sender_identity,
            mailbox_identity=mailbox_identity,
        )

    def _message_event(
        self, envelope: _GmailEnvelope, payload: dict[str, Any]
    ) -> ParsedInboundSurfaceEvent:
        """The message itself: its text, its attachments, what it replies to."""
        data = envelope.data
        headers = envelope.headers
        thread_id = envelope.thread_id
        message_id = envelope.message_id
        sender_identity = envelope.sender_identity
        mailbox_identity = envelope.mailbox_identity

        reply_identity = parse_email_identity(
            headers.get("reply-to"),
            fallback_email=sender_identity.email,
            fallback_name=sender_identity.display_name,
        )

        subject = str(data.get("subject") or headers.get("subject") or "").strip()
        body = (
            payload_text(data, "message_text").strip()
            or _read_email_body(data).strip()
            or _read_gmail_payload_body(data).strip()
            or str(((payload_section(data, "preview")).get("body")) or "").strip()
        )
        # Drop the quoted original. Without this every reply carries the whole
        # thread forward, so by the fourth exchange most of the prompt is the
        # agent re-reading its own earlier messages.
        message_text = (
            f"Email subject: {subject}\n\n{strip_quoted_reply(body, subject)}".strip()
        )

        attachment_candidates = [
            normalized
            for collection in (
                list(data.get("attachments") or []),
                list(data.get("attachment_list") or []),
                _walk_parts(
                    data.get("payload") if isinstance(data.get("payload"), dict) else {}
                ),
            )
            for item in collection
            if isinstance(item, dict)
            for normalized in [_normalize_attachment(item, message_id=message_id)]
            if normalized is not None
        ]
        attachments = _dedupe_attachments(attachment_candidates)

        header_references = [
            ref.strip()
            for ref in payload_text(headers, "references").split()
            if ref.strip()
        ]
        references = [
            str(ref) for ref in list(data.get("references") or header_references) if ref
        ]
        internet_message_id = payload_text(headers, "message-id").strip() or None
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
