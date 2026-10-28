from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OpenCallRequest")


@_attrs_define
class OpenCallRequest:
    """
    Attributes:
        agent_name (None | str | Unset): The pod agent to call in a new conversation; empty calls the pod's default
            agent. A call in an existing chat is with that chat's agent.
        conversation_id (None | Unset | UUID): Hold the call in this chat -- your own, in this pod -- so the call, the
            agent's work and the transcript are that one chat; a call already held there is picked up again. Empty starts a
            new conversation.
        display_name (str | Unset): What the agent is called in a new call's title. Default: 'your teammate'.
        title (None | str | Unset): A new call conversation's title; an existing chat keeps its own.
    """

    agent_name: None | str | Unset = UNSET
    conversation_id: None | Unset | UUID = UNSET
    display_name: str | Unset = "your teammate"
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent_name: None | str | Unset
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

        conversation_id: None | str | Unset
        if isinstance(self.conversation_id, Unset):
            conversation_id = UNSET
        elif isinstance(self.conversation_id, UUID):
            conversation_id = str(self.conversation_id)
        else:
            conversation_id = self.conversation_id

        display_name = self.display_name

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if agent_name is not UNSET:
            field_dict["agent_name"] = agent_name
        if conversation_id is not UNSET:
            field_dict["conversation_id"] = conversation_id
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agent_name", UNSET))

        def _parse_conversation_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                conversation_id_type_0 = UUID(data)

                return conversation_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        conversation_id = _parse_conversation_id(d.pop("conversation_id", UNSET))

        display_name = d.pop("display_name", UNSET)

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        open_call_request = cls(
            agent_name=agent_name,
            conversation_id=conversation_id,
            display_name=display_name,
            title=title,
        )

        open_call_request.additional_properties = d
        return open_call_request

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
