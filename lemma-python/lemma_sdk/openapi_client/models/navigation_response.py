from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.navigation_organization_response import NavigationOrganizationResponse


T = TypeVar("T", bound="NavigationResponse")


@_attrs_define
class NavigationResponse:
    """Everything a sidebar needs, for every organization, in one response.

    Deliberately shallow: apps, agents and roles per pod are the detail endpoint's
    job, because carrying them here would make the payload grow with the content
    of every organization a person happens to belong to.

        Attributes:
            items (list[NavigationOrganizationResponse]):
    """

    items: list[NavigationOrganizationResponse]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.navigation_organization_response import (
            NavigationOrganizationResponse,
        )

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = NavigationOrganizationResponse.from_dict(items_item_data)

            items.append(items_item)

        navigation_response = cls(
            items=items,
        )

        navigation_response.additional_properties = d
        return navigation_response

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
