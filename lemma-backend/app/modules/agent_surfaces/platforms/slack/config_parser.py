"""Parsing for Slack interactions that configure a surface rather than chat."""

from __future__ import annotations

import json
from typing import Any

from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.slack.blocks import (
    CHANNEL_SETUP_ACTION_ID,
    CHANNEL_SETUP_VIEW_CALLBACK_ID,
)
from app.modules.agent_surfaces.platforms.slack.home_blocks import (
    AGENT_DM_ACTION_ID,
    SURFACE_SELECT_ACTION_ID,
)


logger = get_logger(__name__)


class SlackConfigurationParserMixin:
    def parse_channel_setup(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        del headers
        try:
            payload = self._unwrap_payload(payload)
            kind = str(payload.get("type") or "")
            team = payload.get("team") or {}
            actor = str((payload.get("user") or {}).get("id") or "").strip() or None
            tenant_id = str(team.get("id") or payload.get("team_id") or "") or None
            if kind == "block_actions":
                return self._parse_setup_click(payload, tenant_id, actor)
            if kind == "view_submission":
                return self._parse_setup_submission(payload, tenant_id, actor)
            return None
        except AttributeError, KeyError, TypeError, ValueError:
            logger.debug("surface.slack.parse_channel_setup_failed", exc_info=True)
            return None

    def _parse_setup_click(
        self, payload: dict[str, Any], tenant_id: str | None, actor: str | None
    ) -> dict[str, Any] | None:
        actions = [a for a in payload.get("actions") or [] if isinstance(a, dict)]
        trigger_id = str(payload.get("trigger_id") or "").strip()
        parsers = (
            lambda: self._parse_starter_action(actions, tenant_id, actor),
            lambda: self._parse_surface_selection_action(actions, tenant_id, actor),
            lambda: self._parse_channel_setup_action(
                payload, actions, trigger_id, tenant_id, actor
            ),
        )
        for parse in parsers:
            parsed = parse()
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _parse_starter_action(actions, tenant_id, actor):
        starter = next(
            (
                action
                for action in actions
                if str(action.get("action_id") or "").startswith(AGENT_DM_ACTION_ID)
            ),
            None,
        )
        if starter is not None:
            prompt = str(starter.get("value") or "").strip()
            if not prompt or not actor:
                return None
            return {
                "kind": "starter_prompt",
                "prompt": prompt,
                "tenant_id": tenant_id,
                "actor_external_user_id": actor,
            }
        return None

    @staticmethod
    def _parse_surface_selection_action(actions, tenant_id, actor):
        selection = next(
            (
                action
                for action in actions
                if action.get("action_id") == SURFACE_SELECT_ACTION_ID
            ),
            None,
        )
        if selection is None:
            return None
        surface_id = str(selection.get("value") or "").strip()
        if not surface_id or not actor:
            return None
        return {
            "kind": "select_surface",
            "surface_id": surface_id,
            "tenant_id": tenant_id,
            "actor_external_user_id": actor,
        }

    def _parse_channel_setup_action(
        self, payload, actions, trigger_id, tenant_id, actor
    ):
        channel_setup = next(
            (
                action
                for action in actions
                if action.get("action_id") == CHANNEL_SETUP_ACTION_ID
            ),
            None,
        )
        if channel_setup is None:
            return None
        raw_value = str(channel_setup.get("value") or "").strip()
        metadata = self._decode_metadata(raw_value)
        channel_id = str(
            metadata.get("channel_id")
            or raw_value
            or (payload.get("channel") or {}).get("id")
            or ""
        ).strip()
        if not trigger_id or not channel_id:
            return None
        return {
            "kind": "open",
            "trigger_id": trigger_id,
            "channel_id": channel_id,
            "tenant_id": tenant_id,
            "actor_external_user_id": actor,
            "surface_id": str(metadata.get("surface_id") or "").strip() or None,
        }

    def _parse_setup_submission(
        self, payload: dict[str, Any], tenant_id: str | None, actor: str | None
    ) -> dict[str, Any] | None:
        view = payload.get("view") or {}
        values = (view.get("state") or {}).get("values") or {}
        callback_id = view.get("callback_id")
        if callback_id == CHANNEL_SETUP_VIEW_CALLBACK_ID:
            return self._channel_submission(view, values, tenant_id, actor)
        return None

    def _channel_submission(
        self,
        view: dict[str, Any],
        values: dict[str, Any],
        tenant_id: str | None,
        actor: str | None,
    ) -> dict[str, Any] | None:
        """Allowing this channel.

        The channel id rides in ``private_metadata``, either inside an encoded
        object or -- from before that encoding existed -- as the bare id. There
        is nothing else to read: the modal asks whether to answer here, not who
        answers, because the surface has one agent.
        """
        del values
        raw_metadata = str(view.get("private_metadata") or "").strip()
        metadata = self._decode_metadata(raw_metadata)
        channel_id = str(metadata.get("channel_id") or raw_metadata).strip()
        if not channel_id:
            return None
        return {
            "kind": "submit",
            "channel_id": channel_id,
            "tenant_id": tenant_id,
            "actor_external_user_id": actor,
            "surface_id": str(metadata.get("surface_id") or "").strip() or None,
        }

    @staticmethod
    def _decode_metadata(raw_value: str) -> dict[str, Any]:
        if not raw_value.startswith("{"):
            return {}
        decoded = json.loads(raw_value)
        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _selected_value(values: dict[str, Any], block_id: str, action_id: str):
        return (
            ((values.get(block_id) or {}).get(action_id) or {}).get("selected_option")
            or {}
        ).get("value")
