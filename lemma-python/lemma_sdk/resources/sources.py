from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from ..openapi_client.api.channel_sources import (
    channel_source_get,
    channel_source_update,
    channel_telegram_link_create,
    channel_whatsapp_group_start,
)
from ..openapi_client.api.channels import channel_list
from ..openapi_client.models.channel_list_response import ChannelListResponse
from ..openapi_client.models.source_settings_detail_response import (
    SourceSettingsDetailResponse,
)
from ..openapi_client.models.source_settings_response import SourceSettingsResponse
from ..openapi_client.models.source_update_request import SourceUpdateRequest
from ..openapi_client.models.telegram_link_request import TelegramLinkRequest
from ..openapi_client.models.telegram_link_response import TelegramLinkResponse
from ..openapi_client.models.whats_app_group_request import WhatsAppGroupRequest
from ..openapi_client.types import UNSET
from .base import BoundResource, as_uuid


class _Keep:
    def __repr__(self) -> str:
        return "KEEP"


_KEEP: Final = _Keep()


class ChannelSources(BoundResource):
    """The outside places a pod's group channels are fed by, from the bot's
    side: ``pod.channels.sources``.

    A WhatsApp or Telegram group, a Slack or Teams channel, a group DM: each is
    a source of a GROUP channel, read and written in through ``channels``. Here
    is only what the bot keeps about it -- whether people outside the pod are
    answered there, and which member answers for them -- and the two ways to
    bring the bot into a group: ``start_whatsapp_group`` (a business number
    creates groups, it cannot join one; it comes back ``pending`` until
    WhatsApp confirms it) and ``telegram_link`` (a one-use, hour-long link that
    adds the bot to a group the caller picks).
    """

    def list(
        self, surface_name: str, *, limit: int = 50, cursor: str | None = None
    ) -> ChannelListResponse:
        """The groups one bot is in, as channels, each with its
        ``source_settings``; most recently active first."""
        return self._call(
            channel_list,
            self._pod_uuid(),
            source_surface=surface_name,
            cursor=cursor if cursor is not None else UNSET,
            limit=limit,
        )

    def get(
        self, channel_id: str | UUID, source_id: str | UUID
    ) -> SourceSettingsDetailResponse:
        """A group source's settings: who answers its outsiders, and from what."""
        return self._call(
            channel_source_get,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(source_id),
        )

    def update(
        self,
        channel_id: str | UUID,
        source_id: str | UUID,
        *,
        answers_outsiders: bool | _Keep = _KEEP,
        take_over: bool = False,
    ) -> SourceSettingsResponse:
        """Switch outsiders on or off in one group, or take it over (answer
        for its outsiders yourself). Only what is named changes."""
        body: dict[str, Any] = {}
        if not isinstance(answers_outsiders, _Keep):
            body["answers_outsiders"] = answers_outsiders
        if take_over:
            body["owner"] = "me"
        return self._call(
            channel_source_update,
            self._pod_uuid(),
            as_uuid(channel_id),
            as_uuid(source_id),
            body=body,
            body_model=SourceUpdateRequest,
        )

    def start_whatsapp_group(
        self,
        surface_name: str,
        title: str,
        *,
        answers_outsiders: bool = True,
        idempotency_key: str | None = None,
    ) -> SourceSettingsResponse:
        """Start a WhatsApp group with the pod's bot in it: a new group channel
        (``channel_id``). The same ``idempotency_key`` answers with the group
        it started."""
        return self._call(
            channel_whatsapp_group_start,
            self._pod_uuid(),
            body={
                "surface_name": surface_name,
                "title": title,
                "answers_outsiders": answers_outsiders,
            },
            body_model=WhatsAppGroupRequest,
            idempotency_key=idempotency_key if idempotency_key is not None else UNSET,
        )

    def telegram_link(self, surface_name: str) -> TelegramLinkResponse:
        """A one-use, hour-long Telegram link that adds the bot to a group."""
        return self._call(
            channel_telegram_link_create,
            self._pod_uuid(),
            body={"surface_name": surface_name},
            body_model=TelegramLinkRequest,
        )
