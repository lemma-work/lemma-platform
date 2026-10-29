from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.reset_request_scopes_item import ResetRequestScopesItem
from ..types import UNSET, Unset

T = TypeVar("T", bound="ResetRequest")


@_attrs_define
class ResetRequest:
    """
    Attributes:
        scopes (list[ResetRequestScopesItem]):
        confirm (bool | Unset): Required for `home`, which replaces the machine's disk. Default: False.
        forget_logins (bool | Unset): With `home`: do not restore the browser's logins onto the new disk. Logins survive
            every other scope untouched. Default: False.
    """

    scopes: list[ResetRequestScopesItem]
    confirm: bool | Unset = False
    forget_logins: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        scopes = []
        for scopes_item_data in self.scopes:
            scopes_item = scopes_item_data.value
            scopes.append(scopes_item)

        confirm = self.confirm

        forget_logins = self.forget_logins

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "scopes": scopes,
            }
        )
        if confirm is not UNSET:
            field_dict["confirm"] = confirm
        if forget_logins is not UNSET:
            field_dict["forget_logins"] = forget_logins

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        scopes = []
        _scopes = d.pop("scopes")
        for scopes_item_data in _scopes:
            scopes_item = ResetRequestScopesItem(scopes_item_data)

            scopes.append(scopes_item)

        confirm = d.pop("confirm", UNSET)

        forget_logins = d.pop("forget_logins", UNSET)

        reset_request = cls(
            scopes=scopes,
            confirm=confirm,
            forget_logins=forget_logins,
        )

        reset_request.additional_properties = d
        return reset_request

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
