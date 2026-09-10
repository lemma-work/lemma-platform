from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="InstallRequestInitiateSchema")


@_attrs_define
class InstallRequestInitiateSchema:
    """Which account still needs its provider-side installation.

    Attributes:
        account_id (UUID): The connected account the installation is for.
        return_to (None | str | Unset): Path inside the app to come back to. Only a rooted path is accepted; anything
            else is ignored rather than followed.
    """

    account_id: UUID
    return_to: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        account_id = str(self.account_id)

        return_to: None | str | Unset
        if isinstance(self.return_to, Unset):
            return_to = UNSET
        else:
            return_to = self.return_to

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "account_id": account_id,
            }
        )
        if return_to is not UNSET:
            field_dict["return_to"] = return_to

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        account_id = UUID(d.pop("account_id"))

        def _parse_return_to(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        return_to = _parse_return_to(d.pop("return_to", UNSET))

        install_request_initiate_schema = cls(
            account_id=account_id,
            return_to=return_to,
        )

        install_request_initiate_schema.additional_properties = d
        return install_request_initiate_schema

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
