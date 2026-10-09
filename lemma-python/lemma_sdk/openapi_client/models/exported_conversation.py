from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

if TYPE_CHECKING:
    from ..models.exported_message import ExportedMessage


T = TypeVar("T", bound="ExportedConversation")


@_attrs_define
class ExportedConversation:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        messages (list[ExportedMessage]):
        title (None | str):
    """

    created_at: datetime.datetime
    id: UUID
    messages: list[ExportedMessage]
    title: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        messages = []
        for messages_item_data in self.messages:
            messages_item = messages_item_data.to_dict()
            messages.append(messages_item)

        title: None | str
        title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "messages": messages,
                "title": title,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.exported_message import ExportedMessage

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        messages = []
        _messages = d.pop("messages")
        for messages_item_data in _messages:
            messages_item = ExportedMessage.from_dict(messages_item_data)

            messages.append(messages_item)

        def _parse_title(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        title = _parse_title(d.pop("title"))

        exported_conversation = cls(
            created_at=created_at,
            id=id,
            messages=messages,
            title=title,
        )

        exported_conversation.additional_properties = d
        return exported_conversation

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
