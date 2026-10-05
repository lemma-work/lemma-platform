from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="FunctionContactAccessRequest")


@_attrs_define
class FunctionContactAccessRequest:
    """Open a function to contacts, or close it.

    Attributes:
        contacts_invoke (bool): Let a contact's conversation call this function. It runs as the function owner's runs
            do, held to the function's own grants, and the platform puts the asking contact's `contact_id` in its input.
    """

    contacts_invoke: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        contacts_invoke = self.contacts_invoke

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "contacts_invoke": contacts_invoke,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        contacts_invoke = d.pop("contacts_invoke")

        function_contact_access_request = cls(
            contacts_invoke=contacts_invoke,
        )

        function_contact_access_request.additional_properties = d
        return function_contact_access_request

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
