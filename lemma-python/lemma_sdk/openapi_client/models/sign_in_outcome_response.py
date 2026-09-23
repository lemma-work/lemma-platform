from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SignInOutcomeResponse")


@_attrs_define
class SignInOutcomeResponse:
    """
    Attributes:
        origin (str):
        signed_in (bool):
        working (bool | Unset): Whether the site stopped asking for a login straight afterwards. Reported, not enforced:
            the person has already done what was asked, and a site that shows a form at the same address under a neutral
            title reads as still asking. Default: False.
    """

    origin: str
    signed_in: bool
    working: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        origin = self.origin

        signed_in = self.signed_in

        working = self.working

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "origin": origin,
                "signed_in": signed_in,
            }
        )
        if working is not UNSET:
            field_dict["working"] = working

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        origin = d.pop("origin")

        signed_in = d.pop("signed_in")

        working = d.pop("working", UNSET)

        sign_in_outcome_response = cls(
            origin=origin,
            signed_in=signed_in,
            working=working,
        )

        sign_in_outcome_response.additional_properties = d
        return sign_in_outcome_response

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
