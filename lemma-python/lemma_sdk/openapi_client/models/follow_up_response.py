from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="FollowUpResponse")


@_attrs_define
class FollowUpResponse:
    """
    Attributes:
        conversation_id (UUID):
        delivered (bool): Handed to the platform now. False for a web chat, where the message waits for the contact's
            next visit.
        platform (str):
    """

    conversation_id: UUID
    delivered: bool
    platform: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        conversation_id = str(self.conversation_id)

        delivered = self.delivered

        platform = self.platform

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "conversation_id": conversation_id,
                "delivered": delivered,
                "platform": platform,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        conversation_id = UUID(d.pop("conversation_id"))

        delivered = d.pop("delivered")

        platform = d.pop("platform")

        follow_up_response = cls(
            conversation_id=conversation_id,
            delivered=delivered,
            platform=platform,
        )

        follow_up_response.additional_properties = d
        return follow_up_response

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
