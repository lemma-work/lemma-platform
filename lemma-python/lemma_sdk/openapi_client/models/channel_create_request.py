from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.visibility import Visibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_seat import AgentSeat


T = TypeVar("T", bound="ChannelCreateRequest")


@_attrs_define
class ChannelCreateRequest:
    """
    Attributes:
        title (str):
        agents (list[AgentSeat] | Unset):
        members (list[UUID] | Unset): Members to add, by user id.
        topic (None | str | Unset):
        visibility (Visibility | Unset):
    """

    title: str
    agents: list[AgentSeat] | Unset = UNSET
    members: list[UUID] | Unset = UNSET
    topic: None | str | Unset = UNSET
    visibility: Visibility | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        title = self.title

        agents: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = []
            for agents_item_data in self.agents:
                agents_item = agents_item_data.to_dict()
                agents.append(agents_item)

        members: list[str] | Unset = UNSET
        if not isinstance(self.members, Unset):
            members = []
            for members_item_data in self.members:
                members_item = str(members_item_data)
                members.append(members_item)

        topic: None | str | Unset
        if isinstance(self.topic, Unset):
            topic = UNSET
        else:
            topic = self.topic

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "title": title,
            }
        )
        if agents is not UNSET:
            field_dict["agents"] = agents
        if members is not UNSET:
            field_dict["members"] = members
        if topic is not UNSET:
            field_dict["topic"] = topic
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_seat import AgentSeat

        d = dict(src_dict)
        title = d.pop("title")

        _agents = d.pop("agents", UNSET)
        agents: list[AgentSeat] | Unset = UNSET
        if _agents is not UNSET:
            agents = []
            for agents_item_data in _agents:
                agents_item = AgentSeat.from_dict(agents_item_data)

                agents.append(agents_item)

        _members = d.pop("members", UNSET)
        members: list[UUID] | Unset = UNSET
        if _members is not UNSET:
            members = []
            for members_item_data in _members:
                members_item = UUID(members_item_data)

                members.append(members_item)

        def _parse_topic(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        topic = _parse_topic(d.pop("topic", UNSET))

        _visibility = d.pop("visibility", UNSET)
        visibility: Visibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = Visibility(_visibility)

        channel_create_request = cls(
            title=title,
            agents=agents,
            members=members,
            topic=topic,
            visibility=visibility,
        )

        channel_create_request.additional_properties = d
        return channel_create_request

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
