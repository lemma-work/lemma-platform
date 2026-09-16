from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

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
        saved (bool | Unset): Whether the login was kept for next time. Default: False.
        saved_detail (None | str | Unset): Why it was not kept, in words, when it was not.
    """

    origin: str
    signed_in: bool
    saved: bool | Unset = False
    saved_detail: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        origin = self.origin

        signed_in = self.signed_in

        saved = self.saved

        saved_detail: None | str | Unset
        if isinstance(self.saved_detail, Unset):
            saved_detail = UNSET
        else:
            saved_detail = self.saved_detail

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "origin": origin,
                "signed_in": signed_in,
            }
        )
        if saved is not UNSET:
            field_dict["saved"] = saved
        if saved_detail is not UNSET:
            field_dict["saved_detail"] = saved_detail

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        origin = d.pop("origin")

        signed_in = d.pop("signed_in")

        saved = d.pop("saved", UNSET)

        def _parse_saved_detail(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        saved_detail = _parse_saved_detail(d.pop("saved_detail", UNSET))

        sign_in_outcome_response = cls(
            origin=origin,
            signed_in=signed_in,
            saved=saved,
            saved_detail=saved_detail,
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
