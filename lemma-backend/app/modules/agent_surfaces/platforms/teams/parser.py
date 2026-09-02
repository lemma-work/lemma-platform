"""Teams inbound payload parsing (Bot Framework activities and legacy value events)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.platforms.common import (
    payload_any,
    payload_first,
    payload_section,
    payload_text,
    render_attachment_prompt_block,
)

# Key carrying the form callback id inside an Adaptive Card Action.Submit `data`.
TEAMS_FORM_CALLBACK_KEY = "lemma_form_callback_id"
# Key carrying the approval decision on an approval-card Action.Submit `data`.
TEAMS_APPROVAL_DECISION_KEY = "lemma_approval_decision"

_TAG_RE = re.compile(r"<[^>]+>")
_IMG_SRC_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
_IMG_ITEMTYPE_RE = re.compile(r'itemscope="([^"]+)"', re.IGNORECASE)


def strip_html(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def extract_graph_message_attachments(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract attachment descriptors from a Graph API channel message item."""
    results: list[dict[str, Any]] = []
    for att in item.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        content_url = payload_text(att, "contentUrl").strip()
        if not content_url:
            continue
        name = payload_text(att, "name").strip() or None
        content_type = payload_text(att, "contentType").strip()
        file_type = ""
        if name and "." in name:
            file_type = name.rsplit(".", 1)[-1].lower()
        elif "/" in content_type:
            file_type = content_type.split("/")[-1].lower()
        results.append(
            {
                "name": name,
                "download_url": content_url,
                "file_type": file_type,
                "content_type": content_type,
                "size": None,
            }
        )
    body = payload_section(item, "body")
    _append_unique(
        results,
        _image_entry(extract_image_url_from_html(payload_text(body, "content"))),
    )
    return results


def extract_image_url_from_html(html: str) -> str | None:
    match = _IMG_SRC_RE.search(html or "")
    return match.group(1).strip() if match else None


def filename_from_url(url: str) -> str | None:
    candidate = str(url).split("?")[0].rstrip("/").rsplit("/", 1)[-1].strip()
    return candidate or None


def file_type_from_url(url: str) -> str:
    filename = filename_from_url(url)
    if filename and "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return ""


def _image_entry(
    url: str | None, *, name: str | None = None, file_type: str | None = None
) -> dict[str, Any] | None:
    """An inline image as an attachment descriptor, or None when there is no URL."""
    if not url:
        return None
    return {
        "name": name or filename_from_url(url) or "image",
        "download_url": url,
        "file_type": file_type or file_type_from_url(url),
        "content_type": "image/*",
        "size": None,
    }


def _append_unique(results: list[dict[str, Any]], entry: dict[str, Any] | None) -> None:
    """Add an attachment unless its download URL is already in the list.

    One Teams activity can describe the same file three ways -- a rich
    attachment, an inline ``<img>``, and the message HTML -- so every producer
    funnels through here rather than repeating the scan.
    """
    if entry is None:
        return
    if any(item.get("download_url") == entry["download_url"] for item in results):
        return
    results.append(entry)


def _submitted_fields(value: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """The decision and the form inputs carried by one Action.Submit.

    An approval-card submit carries the tapped decision and has no form inputs,
    so the two are mutually exclusive rather than merely usually different.
    """
    decision = str(value.get(TEAMS_APPROVAL_DECISION_KEY) or "").strip() or None
    if decision is not None:
        return decision, {}
    return None, {
        key: item
        for key, item in value.items()
        if key not in (TEAMS_FORM_CALLBACK_KEY, TEAMS_APPROVAL_DECISION_KEY)
    }


def _interaction_reply_target(payload: dict[str, Any]) -> dict[str, str | None]:
    """Where a reply to this submission has to be posted."""
    conversation = payload_section(payload, "conversation")
    return {
        "service_url": payload_text(payload, "serviceUrl").rstrip("/") or None,
        "conversation_id": payload_text(conversation, "id") or None,
        "reply_to_id": payload_text(payload, "replyToId") or None,
    }


@dataclass(frozen=True, slots=True)
class _TeamsRouting:
    """Where a Teams activity lands: which channel, which thread, DM or not.

    Both inbound shapes -- a Bot Framework activity and the legacy ``value``
    event -- work this out identically, quirk for quirk, so they work it out
    here once.
    """

    is_dm: bool
    is_thread_reply: bool
    channel_id: str | None
    external_channel_id: str
    external_thread_id: str
    conversation_id: str
    reply_to_id: str | None

    @property
    def is_addressable(self) -> bool:
        """Whether the message can be placed. Without both ids there is no event."""
        return bool(self.external_channel_id and self.external_thread_id)

    @property
    def conversation_type(self) -> ConversationType:
        return (
            ConversationType.EXTERNAL_DM
            if self.is_dm
            else ConversationType.EXTERNAL_GROUP
        )

    def should_start(self, *, mentioned: bool) -> bool:
        """A DM, an @mention, or a reply inside a thread already under way."""
        return self.is_dm or mentioned or self.is_thread_reply


def _teams_routing(message: dict[str, Any]) -> _TeamsRouting:
    """Read the channel/thread placement out of either activity shape."""
    conversation = payload_section(message, "conversation")
    channel = payload_section(payload_section(message, "channelData"), "channel")
    conversation_id = payload_text(conversation, "id")
    is_dm = payload_text(
        conversation, "conversationType"
    ).lower() == "personal" or not channel.get("id")

    # For channel thread replies Teams may leave channelData.channel.id empty and
    # set conversation.id to a compound string:
    #   "19:<channelId>@thread.tacv2;messageid=<rootMsgId>"
    # The clean channel ID has to come back out so it matches allowed_channel_ids.
    channel_id = payload_text(channel, "id")
    if not channel_id and not is_dm and ";messageid=" in conversation_id:
        channel_id = conversation_id.split(";messageid=")[0]

    return _TeamsRouting(
        is_dm=is_dm,
        is_thread_reply=bool(message.get("replyToId")),
        channel_id=channel_id or None,
        external_channel_id=channel_id or conversation_id,
        # For channels the thread root is the message being replied to, or this
        # message. For DMs conversation.id is stable across the whole chat.
        external_thread_id=(
            conversation_id if is_dm else payload_first(message, "replyToId", "id")
        ),
        conversation_id=conversation_id,
        reply_to_id=None if is_dm else (payload_text(message, "id") or None),
    )


class TeamsMessageParser:
    platform = "TEAMS"

    def parse(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        del headers
        if payload.get("type") in {"message", "messageUpdate"} and payload.get("from"):
            return self._parse_bot_framework_message(payload)

        value = payload.get("value")
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                return self._parse_legacy_value_event(first, payload)
        return None

    def parse_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        """Parse an Adaptive Card Action.Submit (arrives as an activity whose
        ``value`` dict carries our callback key + the input values)."""
        del headers
        value = payload.get("value")
        if not isinstance(value, dict):
            return None
        callback_id = str(value.get(TEAMS_FORM_CALLBACK_KEY) or "").strip()
        if not callback_id:
            return None

        approval_decision, field_values = _submitted_fields(value)
        reply_target = _interaction_reply_target(payload)
        channel_data = payload_section(payload, "channelData")
        tenant = payload_section(channel_data, "tenant")
        channel = payload_section(channel_data, "channel")
        return ParsedSurfaceInteraction(
            platform="TEAMS",
            tenant_id=str(tenant.get("id") or payload.get("tenantId") or "") or None,
            external_channel_id=payload_text(channel, "id") or None,
            external_thread_id=(
                reply_target["reply_to_id"] or reply_target["conversation_id"]
            ),
            # Match the same aad_id-or-bf_user_id precedence identity resolution
            # uses (TeamsSurfaceAdapter.fetch_sender_profile): when the AAD Object
            # ID was resolvable, it — not the bot-framework `id` — is what got
            # stored as the conversation link's external_user_id. Reading only
            # `id` here would reject every native submission's authz check.
            external_user_id=(
                payload_first(payload_section(payload, "from"), "aadObjectId", "id")
                or None
            ),
            callback_id=callback_id,
            values=field_values,
            approval_decision=approval_decision,
            reply_target=reply_target,
            dedup_id=payload_text(payload, "id") or None,
            raw_payload=payload,
        )

    def _parse_bot_framework_message(
        self, payload: dict[str, Any]
    ) -> ParsedInboundSurfaceEvent | None:
        raw_text = strip_html(payload_text(payload, "text"))
        attachments = self.extract_file_attachments(payload)

        # Allow messages that have files but no text body.
        if not raw_text and not attachments:
            return None

        routing = _teams_routing(payload)
        if not routing.is_addressable:
            return None

        return self._event_from_activity(
            message=payload,
            payload=payload,
            sender=payload_section(payload, "from"),
            routing=routing,
            text=self._message_text(raw_text, attachments),
            attachments=attachments,
            service_url=payload_text(payload, "serviceUrl").rstrip("/"),
            mentioned=self._mentioned_bot(payload),
        )

    def _parse_legacy_value_event(
        self, message: dict[str, Any], payload: dict[str, Any]
    ) -> ParsedInboundSurfaceEvent | None:
        raw_text = strip_html(payload_text(message, "text"))
        attachments = self.extract_file_attachments(message)

        if not raw_text and not attachments:
            return None

        routing = _teams_routing(message)
        if not routing.is_addressable:
            return None

        text = self._message_text(raw_text, attachments)
        from_field = payload_section(message, "from")
        return self._event_from_activity(
            message=message,
            payload=payload,
            # This shape nests the person one level deeper.
            sender=payload_section(from_field, "user") or from_field,
            routing=routing,
            text=text,
            attachments=attachments,
            # The outer activity carries the service URL when the message does not.
            service_url=(
                payload_first(message, "serviceUrl")
                or payload_text(payload, "serviceUrl")
            ).rstrip("/"),
            # No entities array in this shape, so an <at> tag is the only signal.
            mentioned="<at>" in text.lower(),
        )

    def _event_from_activity(
        self,
        *,
        message: dict[str, Any],
        payload: dict[str, Any],
        sender: dict[str, Any],
        routing: _TeamsRouting,
        text: str,
        attachments: list[dict[str, Any]],
        service_url: str,
        mentioned: bool,
    ) -> ParsedInboundSurfaceEvent:
        """Build the event both activity shapes resolve to.

        They differ in three fields only -- where the sender sits, where the
        service URL comes from, and how a mention is detected. Everything below
        was duplicated line for line between them until it was pulled here.
        """
        channel_data = payload_section(message, "channelData")
        team = payload_section(channel_data, "team")
        tenant = payload_section(channel_data, "tenant")
        meta = self._build_metadata(
            is_thread_reply=routing.is_thread_reply,
            team_id=payload_text(team, "id") or None,
            team_aad_group_id=payload_text(team, "aadGroupId") or None,
            channel_id=routing.channel_id,
            service_url=service_url or None,
            conversation_id=routing.conversation_id or None,
            reply_to_id=routing.reply_to_id,
            attachments=attachments,
        )
        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=routing.conversation_type,
            tenant_id=(
                payload_first(tenant, "id") or payload_text(payload, "tenantId") or None
            ),
            external_channel_id=routing.external_channel_id,
            external_thread_id=routing.external_thread_id,
            external_message_id=payload_text(message, "id") or None,
            sender_external_user_id=payload_text(sender, "id") or None,
            sender_aad_object_id=payload_text(sender, "aadObjectId") or None,
            sender_display_name=payload_text(sender, "name") or None,
            message_text=text,
            is_dm=routing.is_dm,
            mentioned_agent=mentioned,
            should_start_conversation=routing.should_start(mentioned=mentioned),
            reply_target=self._reply_target(meta),
            metadata=meta,
            raw_payload=payload,
        )

    def extract_file_attachments(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract file attachments from a Bot Framework message payload.

        Teams represents user-shared files as attachments with contentType
        ``application/vnd.microsoft.teams.file.download.info``. Each entry in
        the returned list contains:
          - name:         display filename
          - download_url: authenticated SharePoint URL (requires Graph token to fetch)
          - file_type:    extension string (e.g. "pdf", "docx")
          - content_type: MIME type or Teams-specific content type string
          - size:         file size in bytes (may be None)

        Inline images (contentUrl without Teams file info) are also included so
        the agent can reference them.
        """
        results: list[dict[str, Any]] = []
        for att in payload.get("attachments") or []:
            if isinstance(att, dict):
                _append_unique(results, self._attachment_entry(att))

        # In some Teams activities the only clue is an inline <img src="..."> in the
        # message HTML itself rather than a rich attachment entry.
        _append_unique(
            results,
            _image_entry(extract_image_url_from_html(payload_text(payload, "text"))),
        )
        return results

    def _attachment_entry(self, att: dict[str, Any]) -> dict[str, Any] | None:
        """One attachment as a descriptor, or None when it carries no file.

        Two shapes arrive under the same key: a ``text/html`` card whose only
        payload is an inline image, and a rich attachment with a download URL.
        """
        content_type = payload_text(att, "contentType")
        name = payload_text(att, "name") or None

        if content_type == "text/html":
            html_content = payload_text(att, "content")
            return _image_entry(
                extract_image_url_from_html(html_content),
                name=name,
                file_type=self._extract_image_type_from_html(html_content) or None,
            )

        download_url = self._attachment_download_url(att)
        if not download_url or not self._looks_like_downloadable_attachment(att):
            return None
        content = payload_section(att, "content")
        return {
            "name": name or filename_from_url(download_url) or "attachment",
            "download_url": download_url,
            "file_type": (
                payload_text(content, "fileType").strip()
                or self._file_type_from_name(name)
                or file_type_from_url(download_url)
                or self._file_type_from_content_type(content_type)
            ),
            "content_type": content_type or "application/octet-stream",
            "size": content.get("fileSize"),
        }

    def attachment_prompt_text(self, attachments: list[dict[str, Any]]) -> str:
        return render_attachment_prompt_block(attachments, platform=self.platform)

    def _message_text(self, raw_text: str, attachments: list[dict[str, Any]]) -> str:
        # Build message text so attachment details survive even if a downstream
        # prompt-rendering path only sees the plain message body.
        attachment_text = self.attachment_prompt_text(attachments)
        if raw_text:
            return f"{raw_text}\n\n{attachment_text}" if attachment_text else raw_text
        return attachment_text or "[File shared]"

    def _build_metadata(
        self,
        *,
        is_thread_reply: bool,
        team_id: str | None,
        team_aad_group_id: str | None,
        channel_id: str | None,
        service_url: str | None,
        conversation_id: str | None,
        reply_to_id: str | None,
        attachments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # team_id / channel_id are included so surface tools (e.g. teams_send_file,
        # teams_get_recent_channel_messages) can access them via surface_metadata.
        meta: dict[str, Any] = {
            "is_thread_reply": is_thread_reply,
            "team_id": team_id,
            "team_aad_group_id": team_aad_group_id,
            "channel_id": channel_id,
            "service_url": service_url,
            "conversation_id": conversation_id,
            "reply_to_id": reply_to_id,
        }
        if attachments:
            meta["attachments"] = attachments
        return meta

    def _reply_target(self, meta: dict[str, Any]) -> dict[str, Any]:
        # Bot Framework Connector fields (send_message / typing indicator) plus
        # Graph API fields (enrichment / channel history).
        return {
            key: meta.get(key)
            for key in (
                "service_url",
                "conversation_id",
                "reply_to_id",
                "team_id",
                "team_aad_group_id",
                "channel_id",
            )
        }

    def _mentioned_bot(self, payload: dict[str, Any]) -> bool:
        recipient = payload_section(payload, "recipient")
        recipient_id = payload_text(recipient, "id")
        recipient_name = payload_text(recipient, "name")
        entities = payload.get("entities")
        if isinstance(entities, list):
            # When an entities array is present, trust it: only a mention entity
            # whose `mentioned` id/name matches this bot counts. Returning True
            # for any <at> tag here would wake the bot on @mentions of other
            # users in a channel.
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                if payload_text(entity, "type").lower() != "mention":
                    continue
                mentioned = payload_section(entity, "mentioned")
                if recipient_id and payload_text(mentioned, "id") == recipient_id:
                    return True
                if recipient_name and payload_text(mentioned, "name") == recipient_name:
                    return True
            return False
        # Legacy payload shape with no entities array: fall back to an <at> tag
        # presence check (the only signal available).
        text = payload_text(payload, "text")
        return "<at>" in text.lower()

    def _looks_like_downloadable_attachment(self, att: dict[str, Any]) -> bool:
        download_url = self._attachment_download_url(att)
        if not download_url:
            return False
        content_type = payload_text(att, "contentType").strip().lower()
        if content_type == "text/html":
            return False
        if content_type.startswith("application/vnd.microsoft.card."):
            return False
        return True

    def _attachment_download_url(self, att: dict[str, Any]) -> str:
        content = payload_section(att, "content")
        return str(
            payload_any(content, "downloadUrl", "contentUrl")
            or att.get("contentUrl")
            or ""
        ).strip()

    def _extract_image_type_from_html(self, html: str) -> str:
        match = _IMG_ITEMTYPE_RE.search(html or "")
        if not match:
            return ""
        return match.group(1).strip().lower()

    def _file_type_from_name(self, name: str | None) -> str:
        if name and "." in name:
            return name.rsplit(".", 1)[-1].lower()
        return ""

    def _file_type_from_content_type(self, content_type: str) -> str:
        if "/" in content_type:
            return content_type.split("/")[-1].lower()
        return ""
