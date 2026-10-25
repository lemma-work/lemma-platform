from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.account_pod_usage import AccountPodUsage


T = TypeVar("T", bound="AccountPodUsageResponse")


@_attrs_define
class AccountPodUsageResponse:
    """
    Attributes:
        account_id (UUID):
        items (list[AccountPodUsage]):
    """

    account_id: UUID
    items: list[AccountPodUsage]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        account_id = str(self.account_id)

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "account_id": account_id,
                "items": items,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.account_pod_usage import AccountPodUsage

        d = dict(src_dict)
        account_id = UUID(d.pop("account_id"))

        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = AccountPodUsage.from_dict(items_item_data)

            items.append(items_item)

        account_pod_usage_response = cls(
            account_id=account_id,
            items=items,
        )

        account_pod_usage_response.additional_properties = d
        return account_pod_usage_response

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
