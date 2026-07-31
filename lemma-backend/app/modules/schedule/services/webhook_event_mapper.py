"""Mapping helpers for webhook payload normalization and metadata extraction."""

from __future__ import annotations

from typing import Any, Dict, Optional


class WebhookEventMapper:
    """Normalize webhook payloads and extract matching metadata."""

    def normalize_payload(self, source: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        nested_payload = payload.get("payload")
        if isinstance(nested_payload, dict):
            if payload.get("source") == source or source == "slack":
                return nested_payload
        return payload

    def event_payload_for_source(
        self, source: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        if source == "composio":
            return payload.get("data", {})
        return payload

    def extract_metadata(
        self,
        source: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        metadata: dict[str, Any] = {}

        if source == "slack":
            event = payload.get("event", {})
            metadata.update(
                {
                    "team_id": payload.get("team_id"),
                    "channel_id": event.get("channel"),
                    "event_type": event.get("type"),
                    "thread_ts": event.get("thread_ts") or event.get("ts"),
                    "message_ts": event.get("ts"),
                    "user_id": event.get("user"),
                    "bot_id": event.get("bot_id"),
                    "channel_type": event.get("channel_type"),
                    "text": event.get("text", ""),
                    "source_event_id": payload.get("event_id")
                    or event.get("client_msg_id")
                    or event.get("ts"),
                }
            )
        elif source == "composio":
            data = payload.get("data", {})
            metadata_payload = payload.get("metadata", {})
            metadata.update(
                {
                    "provider_id": data.get("trigger_nano_id")
                    or metadata_payload.get("trigger_id"),
                    "event_type": metadata_payload.get("trigger_slug")
                    or payload.get("type"),
                    "connected_account_id": metadata_payload.get("connected_account_id"),
                    "webhook_event_type": payload.get("webhook_type")
                    or payload.get("type"),
                    "source_event_id": payload.get("id")
                    or metadata_payload.get("log_id")
                    or data.get("event_id"),
                }
            )
        elif source == "jira":
            webhook_event = payload.get("webhookEvent")
            issue = payload.get("issue", {})
            project = issue.get("fields", {}).get("project", {})
            metadata.update(
                {
                    "webhook_event": webhook_event,
                    "project_key": project.get("key"),
                    "project_id": project.get("id"),
                }
            )
        elif source == "email":
            data = payload.get("data", {})
            metadata.update(
                {
                    "thread_id": data.get("thread_id"),
                    "message_id": data.get("message_id"),
                    "source_event_id": data.get("message_id"),
                    "sender_email": data.get("sender"),
                }
            )
        else:
            metadata.update(
                {
                    "workspace_id": payload.get("workspace_id"),
                    "project_id": payload.get("project_id"),
                    "board_id": payload.get("board_id"),
                }
            )

        return {k: v for k, v in metadata.items() if v is not None}
