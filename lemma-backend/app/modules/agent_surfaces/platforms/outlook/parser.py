from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.common import (
    payload_any,
    payload_first,
    payload_text,
)
from app.modules.agent_surfaces.platforms.email_identity import (
    ParsedEmailIdentity,
    parse_email_identity,
)
from app.modules.agent_surfaces.platforms.email_text import (
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


def _first_recipient(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value


def _body_text(data: dict[str, Any]) -> str:
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


def _normalize_attachment(
    raw: dict[str, Any],
    *,
    message_id: str | None,
) -> dict[str, Any] | None:
    attachment_id = payload_any(raw, "attachment_id", "id")
    name = payload_any(raw, "name", "filename", "file_name")
    mime_type = payload_any(raw, "contentType", "mime_type", "content_type")
    content_bytes = payload_any(
        raw, "contentBytes", "content_bytes", "content_bytes_base64"
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
        "is_inline": bool(payload_any(raw, "isInline", "is_inline")),
        "content_id": payload_first(raw, "contentId", "content_id").strip() or None,
        "odata_type": payload_first(raw, "@odata.type", "odata_type").strip() or None,
    }


@dataclass(frozen=True)
class _OutlookEnvelope:
    """What a Graph notification carries, before deciding what it means.

    Nine values, which is why they travel together rather than as nine
    parameters: the two builders below need most of them and neither wants the
    reader to check which.
    """

    data: dict[str, Any]
    headers: dict[str, str]
    thread_id: str
    provider_message_id: str
    internet_message_id: str
    external_message_id: str
    sender_identity: ParsedEmailIdentity
    mailbox_identity: ParsedEmailIdentity
    reply_to_identity: ParsedEmailIdentity

    @property
    def is_complete(self) -> bool:
        """Whether the notification carried the message, not just its id."""
        return bool(
            self.thread_id and self.external_message_id and self.sender_identity.email
        )


class OutlookMessageParser:
    def parse(self, payload: dict[str, Any]) -> ParsedInboundSurfaceEvent | None:
        """A Graph notification, in whichever of its two shapes arrived."""
        envelope = self._envelope(payload)
        if envelope.is_complete:
            return self._message_event(envelope, payload)
        if envelope.provider_message_id:
            return self._pending_fetch_event(envelope, payload)
        return None

    def _envelope(self, payload: dict[str, Any]) -> _OutlookEnvelope:
        """The identities and ids, before deciding which event they make."""
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        headers = _header_map(data.get("internetMessageHeaders"))
        thread_id = payload_first(
            data, "thread_id", "conversation_id", "conversationId", "id"
        ).strip()
        provider_message_id = payload_first(
            data, "message_id", "messageId", "id"
        ).strip()
        internet_message_id = payload_first(
            data, "internet_message_id", "internetMessageId"
        ).strip()
        external_message_id = internet_message_id or provider_message_id
        sender_identity = parse_email_identity(
            payload_any(data, "sender", "from"),
            fallback_email=payload_any(data, "sender_email", "from_email"),
            fallback_name=data.get("sender_name"),
        )
        mailbox_identity = parse_email_identity(
            payload_any(data, "mailbox", "to")
            or _first_recipient(data.get("toRecipients"))
            or _first_recipient(data.get("to_recipients")),
            fallback_email=(
                payload_any(data, "mailbox_email", "to_email", "userPrincipalName")
            ),
        )
        reply_to_identity = parse_email_identity(
            _first_recipient(data.get("replyTo"))
            or _first_recipient(data.get("reply_to")),
            fallback_email=sender_identity.email,
            fallback_name=sender_identity.display_name,
        )
        return _OutlookEnvelope(
            data=data,
            headers=headers,
            thread_id=thread_id,
            provider_message_id=provider_message_id,
            internet_message_id=internet_message_id,
            external_message_id=external_message_id,
            sender_identity=sender_identity,
            mailbox_identity=mailbox_identity,
            reply_to_identity=reply_to_identity,
        )

    def _message_event(
        self, envelope: _OutlookEnvelope, payload: dict[str, Any]
    ) -> ParsedInboundSurfaceEvent:
        """A notification that carried the whole message."""
        data = envelope.data
        headers = envelope.headers
        thread_id = envelope.thread_id
        provider_message_id = envelope.provider_message_id
        internet_message_id = envelope.internet_message_id
        external_message_id = envelope.external_message_id
        sender_identity = envelope.sender_identity
        mailbox_identity = envelope.mailbox_identity
        reply_to_identity = envelope.reply_to_identity
        subject = payload_text(data, "subject").strip()
        # Quoted original trimmed for the same reason as every other
        # provider: a reply should be what the person just wrote.
        body = strip_quoted_reply(_body_text(data).strip(), subject)
        message_text = f"Email subject: {subject}\n\n{body}".strip()
        header_references = [
            ref.strip()
            for ref in payload_text(headers, "references").split()
            if ref.strip()
        ]
        references = [
            str(ref) for ref in list(data.get("references") or header_references) if ref
        ]
        in_reply_to = (
            str(
                data.get("in_reply_to")
                or headers.get("in-reply-to")
                or internet_message_id
                or provider_message_id
            ).strip()
            or None
        )

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

    def _pending_fetch_event(
        self, envelope: _OutlookEnvelope, payload: dict[str, Any]
    ) -> ParsedInboundSurfaceEvent:
        """A trigger webhook that carried only the message id.

        Outlook sends these sparse envelopes, so the event is a typed
        placeholder and the adapter enriches it from Microsoft Graph before
        identity resolution and conversation routing.
        """
        data = envelope.data
        thread_id = envelope.thread_id
        provider_message_id = envelope.provider_message_id
        internet_message_id = envelope.internet_message_id
        external_message_id = envelope.external_message_id
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
                "event_type": payload_text(data, "event_type").strip() or None,
                "requires_message_fetch": True,
            },
            raw_payload=payload,
        )
