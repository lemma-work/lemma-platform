from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.conversation_origin import ConversationOrigin
from ..types import UNSET, Unset

T = TypeVar("T", bound="RecentChatResponse")


@_attrs_define
class RecentChatResponse:
    """
    Attributes:
        conversation_id (UUID):
        last_activity_at (datetime.datetime):
        origin (ConversationOrigin):
        waiting_on_you (bool):
        agent_id (None | Unset | UUID):
        channel_name (None | str | Unset):
        platform (None | str | Unset): The platform a channel's conversation came in on.
        status (None | str | Unset): RUNNING while an agent works in it.
        title (None | str | Unset):
    """

    conversation_id: UUID
    last_activity_at: datetime.datetime
    origin: ConversationOrigin
    waiting_on_you: bool
    agent_id: None | Unset | UUID = UNSET
    channel_name: None | str | Unset = UNSET
    platform: None | str | Unset = UNSET
    status: None | str | Unset = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        conversation_id = str(self.conversation_id)

        last_activity_at = self.last_activity_at.isoformat()

        origin = self.origin.value

        waiting_on_you = self.waiting_on_you

        agent_id: None | str | Unset
        if isinstance(self.agent_id, Unset):
            agent_id = UNSET
        elif isinstance(self.agent_id, UUID):
            agent_id = str(self.agent_id)
        else:
            agent_id = self.agent_id

        channel_name: None | str | Unset
        if isinstance(self.channel_name, Unset):
            channel_name = UNSET
        else:
            channel_name = self.channel_name

        platform: None | str | Unset
        if isinstance(self.platform, Unset):
            platform = UNSET
        else:
            platform = self.platform

        status: None | str | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        else:
            status = self.status

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "conversation_id": conversation_id,
                "last_activity_at": last_activity_at,
                "origin": origin,
                "waiting_on_you": waiting_on_you,
            }
        )
        if agent_id is not UNSET:
            field_dict["agent_id"] = agent_id
        if channel_name is not UNSET:
            field_dict["channel_name"] = channel_name
        if platform is not UNSET:
            field_dict["platform"] = platform
        if status is not UNSET:
            field_dict["status"] = status
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        conversation_id = UUID(d.pop("conversation_id"))

        last_activity_at = isoparse(d.pop("last_activity_at"))

        origin = ConversationOrigin(d.pop("origin"))

        waiting_on_you = d.pop("waiting_on_you")

        def _parse_agent_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                agent_id_type_0 = UUID(data)

                return agent_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        agent_id = _parse_agent_id(d.pop("agent_id", UNSET))

        def _parse_channel_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        channel_name = _parse_channel_name(d.pop("channel_name", UNSET))

        def _parse_platform(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        platform = _parse_platform(d.pop("platform", UNSET))

        def _parse_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        recent_chat_response = cls(
            conversation_id=conversation_id,
            last_activity_at=last_activity_at,
            origin=origin,
            waiting_on_you=waiting_on_you,
            agent_id=agent_id,
            channel_name=channel_name,
            platform=platform,
            status=status,
            title=title,
        )

        recent_chat_response.additional_properties = d
        return recent_chat_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
