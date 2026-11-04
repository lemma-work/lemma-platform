from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="AccountDeletionResponse")


@_attrs_define
class AccountDeletionResponse:
    """What deleting the account did. Every session has ended by the time it is read.

    Attributes:
        deleted (bool): Always true: the account no longer exists, whether this request deleted it or an earlier one
            had.
        organizations_deleted (int): Organizations the person was the only member of, deleted with their pods.
        organizations_left (int): Organizations others are still in, which the person left; their shared work stays
            there under "Deleted user".
        pods_deleted (int):
    """

    deleted: bool
    organizations_deleted: int
    organizations_left: int
    pods_deleted: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        deleted = self.deleted

        organizations_deleted = self.organizations_deleted

        organizations_left = self.organizations_left

        pods_deleted = self.pods_deleted

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "deleted": deleted,
                "organizations_deleted": organizations_deleted,
                "organizations_left": organizations_left,
                "pods_deleted": pods_deleted,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        deleted = d.pop("deleted")

        organizations_deleted = d.pop("organizations_deleted")

        organizations_left = d.pop("organizations_left")

        pods_deleted = d.pop("pods_deleted")

        account_deletion_response = cls(
            deleted=deleted,
            organizations_deleted=organizations_deleted,
            organizations_left=organizations_left,
            pods_deleted=pods_deleted,
        )

        account_deletion_response.additional_properties = d
        return account_deletion_response

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
