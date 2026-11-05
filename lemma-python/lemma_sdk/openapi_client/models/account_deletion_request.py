from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="AccountDeletionRequest")


@_attrs_define
class AccountDeletionRequest:
    """Proof that the person means this account, typed back by them.

    Attributes:
        confirm_email (str): The account's own email address, typed by the person. Compared case-insensitively; anything
            else is refused with ACCOUNT_DELETION_CONFIRMATION_MISMATCH.
    """

    confirm_email: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        confirm_email = self.confirm_email

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "confirm_email": confirm_email,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        confirm_email = d.pop("confirm_email")

        account_deletion_request = cls(
            confirm_email=confirm_email,
        )

        account_deletion_request.additional_properties = d
        return account_deletion_request

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
