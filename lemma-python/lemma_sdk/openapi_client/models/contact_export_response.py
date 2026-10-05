from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.contact_response import ContactResponse
    from ..models.exported_conversation import ExportedConversation


T = TypeVar("T", bound="ContactExportResponse")


@_attrs_define
class ContactExportResponse:
    """Everything the pod holds about one contact, for a request to see it.

    Attributes:
        contact (ContactResponse):
        conversations (list[ExportedConversation]):
    """

    contact: ContactResponse
    conversations: list[ExportedConversation]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        contact = self.contact.to_dict()

        conversations = []
        for conversations_item_data in self.conversations:
            conversations_item = conversations_item_data.to_dict()
            conversations.append(conversations_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "contact": contact,
                "conversations": conversations,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.contact_response import ContactResponse
        from ..models.exported_conversation import ExportedConversation

        d = dict(src_dict)
        contact = ContactResponse.from_dict(d.pop("contact"))

        conversations = []
        _conversations = d.pop("conversations")
        for conversations_item_data in _conversations:
            conversations_item = ExportedConversation.from_dict(conversations_item_data)

            conversations.append(conversations_item)

        contact_export_response = cls(
            contact=contact,
            conversations=conversations,
        )

        contact_export_response.additional_properties = d
        return contact_export_response

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
