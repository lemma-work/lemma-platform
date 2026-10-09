from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.contact_identity_response import ContactIdentityResponse


T = TypeVar("T", bound="ContactResponse")


@_attrs_define
class ContactResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        display_name (None | str | Unset):
        identities (list[ContactIdentityResponse] | Unset):
    """

    created_at: datetime.datetime
    id: UUID
    display_name: None | str | Unset = UNSET
    identities: list[ContactIdentityResponse] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        identities: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.identities, Unset):
            identities = []
            for identities_item_data in self.identities:
                identities_item = identities_item_data.to_dict()
                identities.append(identities_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
            }
        )
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if identities is not UNSET:
            field_dict["identities"] = identities

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.contact_identity_response import ContactIdentityResponse

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        _identities = d.pop("identities", UNSET)
        identities: list[ContactIdentityResponse] | Unset = UNSET
        if _identities is not UNSET:
            identities = []
            for identities_item_data in _identities:
                identities_item = ContactIdentityResponse.from_dict(
                    identities_item_data
                )

                identities.append(identities_item)

        contact_response = cls(
            created_at=created_at,
            id=id,
            display_name=display_name,
            identities=identities,
        )

        contact_response.additional_properties = d
        return contact_response

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
