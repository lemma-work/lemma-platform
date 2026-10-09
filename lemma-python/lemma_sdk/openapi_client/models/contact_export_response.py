from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.contact_response import ContactResponse
    from ..models.contact_row import ContactRow
    from ..models.exported_conversation import ExportedConversation


T = TypeVar("T", bound="ContactExportResponse")


@_attrs_define
class ContactExportResponse:
    """Everything the pod holds about one contact, a page at a time.

    Their conversations come first, then their rows in the pod's
    contact-owned tables. Follow `next_cursor` until it is absent.

        Attributes:
            contact (ContactResponse):
            conversations (list[ExportedConversation]):
            next_cursor (None | str | Unset): Pass as `cursor` for the next page; absent on the last.
            rows (list[ContactRow] | Unset):
    """

    contact: ContactResponse
    conversations: list[ExportedConversation]
    next_cursor: None | str | Unset = UNSET
    rows: list[ContactRow] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        contact = self.contact.to_dict()

        conversations = []
        for conversations_item_data in self.conversations:
            conversations_item = conversations_item_data.to_dict()
            conversations.append(conversations_item)

        next_cursor: None | str | Unset
        if isinstance(self.next_cursor, Unset):
            next_cursor = UNSET
        else:
            next_cursor = self.next_cursor

        rows: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.rows, Unset):
            rows = []
            for rows_item_data in self.rows:
                rows_item = rows_item_data.to_dict()
                rows.append(rows_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "contact": contact,
                "conversations": conversations,
            }
        )
        if next_cursor is not UNSET:
            field_dict["next_cursor"] = next_cursor
        if rows is not UNSET:
            field_dict["rows"] = rows

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.contact_response import ContactResponse
        from ..models.contact_row import ContactRow
        from ..models.exported_conversation import ExportedConversation

        d = dict(src_dict)
        contact = ContactResponse.from_dict(d.pop("contact"))

        conversations = []
        _conversations = d.pop("conversations")
        for conversations_item_data in _conversations:
            conversations_item = ExportedConversation.from_dict(conversations_item_data)

            conversations.append(conversations_item)

        def _parse_next_cursor(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor", UNSET))

        _rows = d.pop("rows", UNSET)
        rows: list[ContactRow] | Unset = UNSET
        if _rows is not UNSET:
            rows = []
            for rows_item_data in _rows:
                rows_item = ContactRow.from_dict(rows_item_data)

                rows.append(rows_item)

        contact_export_response = cls(
            contact=contact,
            conversations=conversations,
            next_cursor=next_cursor,
            rows=rows,
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
