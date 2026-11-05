from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.shared_resource_body import SharedResourceBody


T = TypeVar("T", bound="ConnectPodRequest")


@_attrs_define
class ConnectPodRequest:
    """
    Attributes:
        shares (list[SharedResourceBody] | Unset): What the asking pod may read here, past what is Public.
    """

    shares: list[SharedResourceBody] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        shares: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.shares, Unset):
            shares = []
            for shares_item_data in self.shares:
                shares_item = shares_item_data.to_dict()
                shares.append(shares_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if shares is not UNSET:
            field_dict["shares"] = shares

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.shared_resource_body import SharedResourceBody

        d = dict(src_dict)
        _shares = d.pop("shares", UNSET)
        shares: list[SharedResourceBody] | Unset = UNSET
        if _shares is not UNSET:
            shares = []
            for shares_item_data in _shares:
                shares_item = SharedResourceBody.from_dict(shares_item_data)

                shares.append(shares_item)

        connect_pod_request = cls(
            shares=shares,
        )

        connect_pod_request.additional_properties = d
        return connect_pod_request

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
