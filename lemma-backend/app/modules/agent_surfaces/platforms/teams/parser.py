"""Teams inbound payload parsing (Bot Framework activities and legacy value events)."""

from __future__ import annotations

import re
from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.platforms.common import (
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
    inline_url = extract_image_url_from_html(payload_text(body, "content"))
    if inline_url and not any(
        existing.get("download_url") == inline_url for existing in results
    ):
        results.append(
            {
                "name": filename_from_url(inline_url) or "image",
                "download_url": inline_url,
                "file_type": file_type_from_url(inline_url),
                "content_type": "image/*",
                "size": None,
            }
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
        # An approval-card submit carries the tapped decision; it has no form
        # inputs, so its values map is empty.
        approval_decision = (
            str(value.get(TEAMS_APPROVAL_DECISION_KEY) or "").strip() or None
        )
        field_values = (
            {}
            if approval_decision is not None
            else {
                k: v
                for k, v in value.items()
                if k not in (TEAMS_FORM_CALLBACK_KEY, TEAMS_APPROVAL_DECISION_KEY)
            }
        )
        from_user = payload_section(payload, "from")
        conversation = payload_section(payload, "conversation")
        channel_data = payload_section(payload, "channelData")
        tenant = payload_section(channel_data, "tenant")
        channel = payload_section(channel_data, "channel")
        service_url = payload_text(payload, "serviceUrl").rstrip("/") or None
        conversation_id = payload_text(conversation, "id") or None
        reply_to_id = payload_text(payload, "replyToId") or None
        return ParsedSurfaceInteraction(
            platform="TEAMS",
            tenant_id=str(tenant.get("id") or payload.get("tenantId") or "") or None,
            external_channel_id=payload_text(channel, "id") or None,
            external_thread_id=reply_to_id or conversation_id,
            # Match the same aad_id-or-bf_user_id precedence identity resolution
            # uses (TeamsSurfaceAdapter.fetch_sender_profile): when the AAD Object
            # ID was resolvable, it — not the bot-framework `id` — is what got
            # stored as the conversation link's external_user_id. Reading only
            # `id` here would reject every native submission's authz check.
            external_user_id=(
                str(from_user.get("aadObjectId") or from_user.get("id") or "") or None
            ),
            callback_id=callback_id,
            values=field_values,
            approval_decision=approval_decision,
            reply_target={
                "service_url": service_url,
                "conversation_id": conversation_id,
                "reply_to_id": reply_to_id,
            },
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

        conversation = payload_section(payload, "conversation")
        from_user = payload_section(payload, "from")
        channel_data = payload_section(payload, "channelData")
        team = payload_section(channel_data, "team")
        channel = payload_section(channel_data, "channel")
        tenant = payload_section(channel_data, "tenant")

        is_thread_reply = bool(payload.get("replyToId"))
        is_dm = str(
            conversation.get("conversationType") or ""
        ).lower() == "personal" or not channel.get("id")

        # For channel thread replies Teams may leave channelData.channel.id empty and
        # set conversation.id to a compound string:
        #   "19:<channelId>@thread.tacv2;messageid=<rootMsgId>"
        # We must extract the clean channel ID so it matches allowed_channel_ids.
        channel_id_raw = payload_text(channel, "id")
        if not channel_id_raw and not is_dm:
            conv_id = payload_text(conversation, "id")
            if ";messageid=" in conv_id:
                channel_id_raw = conv_id.split(";messageid=")[0]

        external_channel_id = str(channel_id_raw or conversation.get("id") or "")
        # For channels: thread root is the message being replied to (replyToId) or the
        # message itself. For DMs: the conversation ID is stable across the whole chat.
        external_thread_id = (
            payload_text(conversation, "id")
            if is_dm
            else str(payload.get("replyToId") or payload.get("id") or "")
        )
        if not external_channel_id or not external_thread_id:
            return None

        service_url = payload_text(payload, "serviceUrl").rstrip("/")
        conversation_id = payload_text(conversation, "id")
        # reply_to_id lets Bot Framework thread our reply under the original message.
        reply_to_id = payload_text(payload, "id") if not is_dm else None

        text = self._message_text(raw_text, attachments)
        mentioned = self._mentioned_bot(payload)
        team_id = payload_text(team, "id") or None
        team_aad_group_id = payload_text(team, "aadGroupId") or None
        meta = self._build_metadata(
            is_thread_reply=is_thread_reply,
            team_id=team_id,
            team_aad_group_id=team_aad_group_id,
            channel_id=channel_id_raw or None,
            service_url=service_url or None,
            conversation_id=conversation_id or None,
            reply_to_id=reply_to_id or None,
            attachments=attachments,
        )

        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=(
                ConversationType.EXTERNAL_DM
                if is_dm
                else ConversationType.EXTERNAL_GROUP
            ),
            tenant_id=str(tenant.get("id") or payload.get("tenantId") or "") or None,
            external_channel_id=external_channel_id,
            external_thread_id=external_thread_id,
            external_message_id=payload_text(payload, "id") or None,
            sender_external_user_id=payload_text(from_user, "id") or None,
            sender_aad_object_id=payload_text(from_user, "aadObjectId") or None,
            sender_display_name=payload_text(from_user, "name") or None,
            message_text=text,
            is_dm=is_dm,
            mentioned_agent=mentioned,
            should_start_conversation=is_dm or mentioned or is_thread_reply,
            reply_target=self._reply_target(meta),
            metadata=meta,
            raw_payload=payload,
        )

    def _parse_legacy_value_event(
        self, message: dict[str, Any], payload: dict[str, Any]
    ) -> ParsedInboundSurfaceEvent | None:
        raw_text = strip_html(payload_text(message, "text"))
        attachments = self.extract_file_attachments(message)

        if not raw_text and not attachments:
            return None

        sender = (payload_section(message, "from")).get("user", {}) or payload_section(
            message, "from"
        )
        conversation = payload_section(message, "conversation")
        channel_data = payload_section(message, "channelData")
        channel = payload_section(channel_data, "channel")
        team = payload_section(channel_data, "team")
        tenant = payload_section(channel_data, "tenant")

        is_thread_reply = bool(message.get("replyToId"))
        is_dm = str(
            conversation.get("conversationType") or ""
        ).lower() == "personal" or not channel.get("id")

        channel_id_raw = payload_text(channel, "id")
        if not channel_id_raw and not is_dm:
            conv_id = payload_text(conversation, "id")
            if ";messageid=" in conv_id:
                channel_id_raw = conv_id.split(";messageid=")[0]

        external_channel_id = str(channel_id_raw or conversation.get("id") or "")
        external_thread_id = (
            payload_text(conversation, "id")
            if is_dm
            else str(message.get("replyToId") or message.get("id") or "")
        )
        if not external_channel_id or not external_thread_id:
            return None

        service_url = str(
            message.get("serviceUrl") or payload.get("serviceUrl") or ""
        ).rstrip("/")
        conversation_id = payload_text(conversation, "id")
        reply_to_id = payload_text(message, "id") if not is_dm else None

        text = self._message_text(raw_text, attachments)
        mentioned = "<at>" in text.lower()
        team_id = payload_text(team, "id") or None
        team_aad_group_id = payload_text(team, "aadGroupId") or None
        meta = self._build_metadata(
            is_thread_reply=is_thread_reply,
            team_id=team_id,
            team_aad_group_id=team_aad_group_id,
            channel_id=channel_id_raw or None,
            service_url=service_url or None,
            conversation_id=conversation_id or None,
            reply_to_id=reply_to_id or None,
            attachments=attachments,
        )

        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=(
                ConversationType.EXTERNAL_DM
                if is_dm
                else ConversationType.EXTERNAL_GROUP
            ),
            tenant_id=str(tenant.get("id") or payload.get("tenantId") or "") or None,
            external_channel_id=external_channel_id,
            external_thread_id=external_thread_id,
            external_message_id=payload_text(message, "id") or None,
            sender_external_user_id=payload_text(sender, "id") or None,
            sender_aad_object_id=payload_text(sender, "aadObjectId") or None,
            sender_display_name=payload_text(sender, "name") or None,
            message_text=text,
            is_dm=is_dm,
            mentioned_agent=mentioned,
            should_start_conversation=is_dm or mentioned or is_thread_reply,
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
            if not isinstance(att, dict):
                continue
            content_type = payload_text(att, "contentType")
            name = payload_text(att, "name") or None
            content = payload_section(att, "content")

            if content_type == "text/html":
                html_content = payload_text(att, "content")
                image_url = extract_image_url_from_html(html_content)
                if image_url and not any(
                    existing.get("download_url") == image_url for existing in results
                ):
                    results.append(
                        {
                            "name": name or filename_from_url(image_url) or "image",
                            "download_url": image_url,
                            "file_type": (
                                self._extract_image_type_from_html(html_content)
                                or file_type_from_url(image_url)
                            ),
                            "content_type": "image/*",
                            "size": None,
                        }
                    )
                continue

            download_url = self._attachment_download_url(att)
            if download_url and self._looks_like_downloadable_attachment(att):
                if not any(
                    existing.get("download_url") == download_url for existing in results
                ):
                    file_type = (
                        payload_text(content, "fileType").strip()
                        or self._file_type_from_name(name)
                        or file_type_from_url(download_url)
                        or self._file_type_from_content_type(content_type)
                    )
                    results.append(
                        {
                            "name": name
                            or filename_from_url(download_url)
                            or "attachment",
                            "download_url": download_url,
                            "file_type": file_type,
                            "content_type": content_type or "application/octet-stream",
                            "size": content.get("fileSize"),
                        }
                    )

        # In some Teams activities the only clue is an inline <img src="..."> in the
        # message HTML itself rather than a rich attachment entry.
        inline_url = extract_image_url_from_html(payload_text(payload, "text"))
        if inline_url and not any(
            existing.get("download_url") == inline_url for existing in results
        ):
            results.append(
                {
                    "name": filename_from_url(inline_url) or "image",
                    "download_url": inline_url,
                    "file_type": file_type_from_url(inline_url),
                    "content_type": "image/*",
                    "size": None,
                }
            )
        return results

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
            content.get("downloadUrl")
            or content.get("contentUrl")
            or att.get("contentUrl")
            or ""
        ).strip()

    def _extract_image_type_from_html(self, html: str) -> str:
        match = _IMG_ITEMTYPE_RE.search(html or "")
        if not match:
            return ""
        raw = match.group(1).strip().lower()
        return raw

    def _file_type_from_name(self, name: str | None) -> str:
        if name and "." in name:
            return name.rsplit(".", 1)[-1].lower()
        return ""

    def _file_type_from_content_type(self, content_type: str) -> str:
        if "/" in content_type:
            return content_type.split("/")[-1].lower()
        return ""
