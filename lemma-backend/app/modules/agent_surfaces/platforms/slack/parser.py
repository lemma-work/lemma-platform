from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.platforms.common import render_attachment_prompt_block
from app.modules.agent_surfaces.platforms.slack.models import (
    SLACK_APPROVAL_DECISION_BY_ACTION_ID as _APPROVAL_DECISION_BY_ACTION_ID,
    SLACK_FORM_SUBMIT_ACTION_ID as _FORM_SUBMIT_ACTION_ID,
    SlackChannelMessageSnapshot,
    SlackFileAttachment,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class SlackMessageParser:
    platform = "SLACK"

    def parse(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        try:
            del headers
            payload = self._unwrap_payload(payload)
            event = payload.get("event") or {}
            if payload.get("type") != "event_callback":
                return None

            event_type = str(event.get("type") or "")
            if event_type not in {"message", "app_mention"}:
                return None

            subtype = str(event.get("subtype") or "")
            if event.get("bot_id") or subtype in {
                "bot_message",
                "message_changed",
                "message_deleted",
                "channel_join",
                "channel_leave",
                "thread_broadcast",
            }:
                return None

            channel_id = str(event.get("channel") or "").strip()
            attachments = self.extract_file_attachments(event)
            raw_text = str(
                event.get("text") or self._extract_text_from_blocks(event) or ""
            ).strip()
            attachment_text = render_attachment_prompt_block(
                attachments,
                platform=self.platform,
            )
            text = self._message_text(raw_text, attachment_text)
            if not channel_id or not text:
                return None

            channel_type = str(event.get("channel_type") or "")
            is_dm = channel_type == "im" or channel_id.startswith("D")
            assistant_thread = event.get("assistant_thread") or {}
            assistant_thread_ts = str(assistant_thread.get("thread_ts") or "") or None
            thread_ts = event.get("thread_ts") or assistant_thread_ts
            ts = event.get("ts")
            is_thread_reply = bool(thread_ts and thread_ts != ts)
            mentioned_user_ids = self._extract_mentioned_user_ids(
                event, raw_text or text
            )
            mentioned_agent = event_type == "app_mention" or bool(mentioned_user_ids)
            external_thread_id = str(thread_ts or ts or "").strip()
            if not external_thread_id:
                return None

            metadata: dict[str, Any] = {
                "event_type": event_type,
                "event_subtype": subtype or None,
                "is_thread_reply": is_thread_reply,
                "channel_type": channel_type,
                "mentioned_user_ids": mentioned_user_ids,
                "assistant_thread_present": bool(assistant_thread),
                "assistant_thread_action_token": (
                    str(assistant_thread.get("action_token") or "") or None
                ),
            }
            if attachments:
                metadata["attachments"] = [
                    attachment.model_dump(mode="json", exclude_none=True)
                    for attachment in attachments
                ]

            return ParsedInboundSurfaceEvent(
                platform=self.platform,
                conversation_type=(
                    ConversationType.EXTERNAL_DM
                    if is_dm
                    else ConversationType.EXTERNAL_GROUP
                ),
                tenant_id=str(payload.get("team_id") or "").strip() or None,
                external_channel_id=channel_id,
                external_thread_id=external_thread_id,
                external_message_id=str(ts or "").strip() or None,
                sender_external_user_id=str(event.get("user") or "").strip() or None,
                sender_display_name=None,
                message_text=text,
                is_dm=is_dm,
                mentioned_agent=mentioned_agent,
                should_start_conversation=(
                    is_dm
                    or is_thread_reply
                    or event_type == "app_mention"
                    or mentioned_agent
                ),
                reply_target={
                    "channel": channel_id,
                    "thread_ts": str(thread_ts or ts or "").strip(),
                },
                metadata={
                    key: value for key, value in metadata.items() if value is not None
                },
                raw_payload=payload,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.parser.slack_parser_normalize_inbound_event.propagated',
                exc_info=True,
            )
            raise

    def parse_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        """Parse a Slack ``block_actions`` form submission into an interaction."""
        del headers
        try:
            payload = self._unwrap_payload(payload)
            if payload.get("type") != "block_actions":
                return None
            actions = payload.get("actions") or []
            # An approval button tap (Approve/Deny/Approve-for-session) carries the
            # decision in its action_id and the callback id in its value.
            approval = next(
                (
                    a
                    for a in actions
                    if isinstance(a, dict)
                    and str(a.get("action_id") or "") in _APPROVAL_DECISION_BY_ACTION_ID
                ),
                None,
            )
            submit = next(
                (
                    a
                    for a in actions
                    if isinstance(a, dict)
                    and a.get("action_id") == _FORM_SUBMIT_ACTION_ID
                ),
                None,
            )
            action = approval or submit
            if action is None:
                return None
            callback_id = str(action.get("value") or "").strip()
            if not callback_id:
                return None

            approval_decision = (
                _APPROVAL_DECISION_BY_ACTION_ID.get(str(action.get("action_id") or ""))
                if approval is not None
                else None
            )
            if approval_decision is not None:
                values: dict[str, Any] = {}
            else:
                state_values = (payload.get("state") or {}).get("values") or {}
                values = _flatten_block_state_values(state_values)

            user = payload.get("user") or {}
            channel = payload.get("channel") or {}
            team = payload.get("team") or {}
            container = payload.get("container") or {}
            message = payload.get("message") or {}
            channel_id = str(channel.get("id") or "").strip() or None
            message_ts = str(
                container.get("message_ts") or message.get("ts") or ""
            ).strip()
            thread_ts = (
                str(message.get("thread_ts") or message_ts or "").strip() or None
            )
            return ParsedSurfaceInteraction(
                platform="SLACK",
                tenant_id=str(team.get("id") or payload.get("team_id") or "") or None,
                external_channel_id=channel_id,
                external_thread_id=thread_ts,
                external_user_id=str(user.get("id") or "").strip() or None,
                callback_id=callback_id,
                values=values,
                approval_decision=approval_decision,
                reply_target={"channel": channel_id, "thread_ts": thread_ts},
                dedup_id=f"{message_ts}:{action.get('action_ts') or ''}",
                raw_payload=payload,
            )
        except Exception:
            logger.debug(
                "surface.slack.parse_failed",
                exc_info=True,
            )
            return None

    def normalize_context_message(
        self,
        item: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        try:
            if not isinstance(item, dict):
                return None

            attachments = self.extract_file_attachments(item)
            raw_text = str(item.get("text") or "").strip()
            attachment_text = render_attachment_prompt_block(
                attachments,
                platform=self.platform,
            )
            text = self._message_text(raw_text, attachment_text)
            if not text:
                return None

            snapshot = SlackChannelMessageSnapshot(
                message_id=str(item.get("ts") or "").strip() or None,
                user=str(item.get("user") or "").strip() or None,
                display_name=(
                    ((item.get("user_profile") or {}).get("display_name"))
                    or ((item.get("user_profile") or {}).get("real_name"))
                    or item.get("username")
                ),
                text=text,
                thread_ts=str(item.get("thread_ts") or "").strip() or None,
                attachments=attachments,
            )
            data = snapshot.model_dump(mode="json", exclude_none=True)
            if not data.get("attachments"):
                data.pop("attachments", None)
            return data
        except Exception:
            logger.debug(
                'agent_surfaces.parser.slack_parser_normalize_context_message.propagated',
                exc_info=True,
            )
            raise

    def extract_file_attachments(
        self,
        event: dict[str, Any],
    ) -> list[SlackFileAttachment]:
        attachments: list[SlackFileAttachment] = []
        for raw in event.get("files") or []:
            if not isinstance(raw, dict):
                continue
            file_id = str(raw.get("id") or "").strip() or None
            download_url = (
                str(
                    raw.get("url_private_download") or raw.get("url_private") or ""
                ).strip()
                or None
            )
            permalink = str(raw.get("permalink") or "").strip() or None
            name = (
                str(raw.get("name") or raw.get("title") or "").strip()
                or self._filename_from_url(download_url or permalink or "")
                or None
            )
            if not name and not file_id and not download_url:
                continue
            attachments.append(
                SlackFileAttachment(
                    id=file_id,
                    name=name,
                    download_url=download_url,
                    permalink=permalink,
                    content_type=str(raw.get("mimetype") or "").strip(),
                    file_type=str(raw.get("filetype") or "").strip(),
                    mime_type=str(raw.get("mimetype") or "").strip() or None,
                    size=raw.get("size"),
                )
            )
        return attachments

    def _unwrap_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        nested_payload = payload.get("payload")
        if (
            isinstance(nested_payload, dict)
            and nested_payload.get("type") == "event_callback"
        ):
            return nested_payload
        nested_data = payload.get("data")
        if (
            isinstance(nested_data, dict)
            and nested_data.get("type") == "event_callback"
        ):
            return nested_data
        return payload

    def _extract_text_from_blocks(self, event: dict[str, Any]) -> str:
        parts: list[str] = []
        for block in event.get("blocks") or []:
            for element in block.get("elements") or []:
                for child in element.get("elements") or []:
                    child_type = child.get("type")
                    if child_type == "text" and child.get("text"):
                        parts.append(str(child["text"]))
                    if child_type == "user" and child.get("user_id"):
                        parts.append(f"<@{child['user_id']}>")
        return "".join(parts).strip()

    def _extract_mentioned_user_ids(
        self,
        event: dict[str, Any],
        text: str,
    ) -> list[str]:
        mentioned_user_ids: list[str] = []
        for token in text.split("<@")[1:]:
            user_id = token.split(">", 1)[0].strip()
            if user_id:
                mentioned_user_ids.append(user_id)

        for block in event.get("blocks") or []:
            for element in block.get("elements") or []:
                for child in element.get("elements") or []:
                    if child.get("type") == "user" and child.get("user_id"):
                        mentioned_user_ids.append(str(child["user_id"]))

        deduped: list[str] = []
        for user_id in mentioned_user_ids:
            if user_id not in deduped:
                deduped.append(user_id)
        return deduped

    def _message_text(self, raw_text: str, attachment_text: str) -> str:
        if raw_text and attachment_text:
            return f"{raw_text}\n\n{attachment_text}"
        if raw_text:
            return raw_text
        return attachment_text or "[File shared]"

    def _filename_from_url(self, value: str) -> str:
        tail = str(value or "").rstrip("/").split("/")[-1]
        return tail.strip()


def _extract_slack_input_value(val: Any) -> Any:
    """Pull the submitted value out of one Slack block-state element."""
    if not isinstance(val, dict):
        return val
    if val.get("value") is not None:
        return val.get("value")
    selected_option = val.get("selected_option")
    if isinstance(selected_option, dict):
        return selected_option.get("value")
    if "selected_options" in val:
        return [
            opt.get("value")
            for opt in (val.get("selected_options") or [])
            if isinstance(opt, dict)
        ]
    if val.get("selected_date") is not None:
        return val.get("selected_date")
    return None


def _flatten_block_state_values(state_values: dict[str, Any]) -> dict[str, Any]:
    """Flatten Slack ``state.values`` ({block_id: {action_id: element}}) to
    ``{field_name: value}`` (block_id == field name in our forms)."""
    out: dict[str, Any] = {}
    for block_id, actions in (state_values or {}).items():
        if not isinstance(actions, dict) or not actions:
            continue
        element = next(iter(actions.values()))
        out[str(block_id)] = _extract_slack_input_value(element)
    return out
