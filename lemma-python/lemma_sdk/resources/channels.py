from __future__ import annotations

from typing import Literal
from uuid import UUID

from ..openapi_client.api.channels import (
    channel_get,
    channel_handler_release,
    channel_handler_take,
    channel_list,
    channel_mark_read,
    channel_message_list,
)
from ..openapi_client.models.channel_detail_response import ChannelDetailResponse
from ..openapi_client.models.channel_kind import ChannelKind
from ..openapi_client.models.channel_list_response import ChannelListResponse
from ..openapi_client.models.channel_messages_response import ChannelMessagesResponse
from ..openapi_client.models.channel_response import ChannelResponse
from ..openapi_client.types import UNSET
from .base import BoundResource, as_uuid


class PodChannels(BoundResource):
    """The places a pod's members, contacts and agents talk.

    A channel is one shared timeline fed by any number of outside sources -- a
    WhatsApp chat, a Telegram group, a Slack channel, an email thread, a web
    chat. ``DIRECT`` is one member and the pod, ``CONTACT`` one contact and
    the pod, ``GROUP`` a chat several people read. Another member's direct
    channel is never listed.
    """

    def list(
        self,
        *,
        kind: Literal["DIRECT", "CONTACT", "GROUP"] | None = None,
        limit: int = 50,
        before: str | None = None,
    ) -> ChannelListResponse:
        """The pod's channels, most recently active first.

        Page by passing the last page's ``next_before`` as ``before``.
        """
        return self._call(
            channel_list,
            self._pod_uuid(),
            kind=ChannelKind(kind) if kind is not None else UNSET,
            limit=limit,
            before=before if before is not None else UNSET,
        )

    def get(self, channel_id: str | UUID) -> ChannelDetailResponse:
        """One channel, with its sources and participants."""
        return self._call(channel_get, self._pod_uuid(), as_uuid(channel_id))

    def messages(
        self, channel_id: str | UUID, *, limit: int = 50, before: str | None = None
    ) -> ChannelMessagesResponse:
        """The channel's timeline, oldest first: who said what, when and where.

        The newest page unless ``before`` is the previous page's
        ``next_before``.
        """
        return self._call(
            channel_message_list,
            self._pod_uuid(),
            as_uuid(channel_id),
            limit=limit,
            before=before if before is not None else UNSET,
        )

    def mark_read(self, channel_id: str | UUID) -> None:
        """Note that the caller has read the channel up to now."""
        self._call(channel_mark_read, self._pod_uuid(), as_uuid(channel_id))

    def take(self, channel_id: str | UUID) -> ChannelResponse:
        """Answer a contact's channel by hand: its agents stop answering there.

        Takes ``contact.message``.
        """
        return self._call(channel_handler_take, self._pod_uuid(), as_uuid(channel_id))

    def release(self, channel_id: str | UUID) -> ChannelResponse:
        """Hand a contact's channel back to its agents."""
        return self._call(
            channel_handler_release, self._pod_uuid(), as_uuid(channel_id)
        )
