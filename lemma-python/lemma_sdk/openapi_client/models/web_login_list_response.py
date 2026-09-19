from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.web_login_response import WebLoginResponse


T = TypeVar("T", bound="WebLoginListResponse")


@_attrs_define
class WebLoginListResponse:
    """
    Attributes:
        items (list[WebLoginResponse]):
        sleeping (bool | Unset): True when the computer is paused and was not woken to answer. Items are empty; its
            browser still holds whatever it held. Default: False.
    """

    items: list[WebLoginResponse]
    sleeping: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        sleeping = self.sleeping

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
            }
        )
        if sleeping is not UNSET:
            field_dict["sleeping"] = sleeping

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.web_login_response import WebLoginResponse

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = WebLoginResponse.from_dict(items_item_data)

            items.append(items_item)

        sleeping = d.pop("sleeping", UNSET)

        web_login_list_response = cls(
            items=items,
            sleeping=sleeping,
        )

        web_login_list_response.additional_properties = d
        return web_login_list_response

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
