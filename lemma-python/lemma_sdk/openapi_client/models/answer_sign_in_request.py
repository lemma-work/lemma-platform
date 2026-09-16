from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AnswerSignInRequest")


@_attrs_define
class AnswerSignInRequest:
    """
    Attributes:
        signed_in (bool): True when the person says they have signed in; false when they cannot right now.
        force (bool | Unset): Save whatever the browser holds even though it does not look signed in. For sites the
            check reads wrongly. Default: False.
    """

    signed_in: bool
    force: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        signed_in = self.signed_in

        force = self.force

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "signed_in": signed_in,
            }
        )
        if force is not UNSET:
            field_dict["force"] = force

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        signed_in = d.pop("signed_in")

        force = d.pop("force", UNSET)

        answer_sign_in_request = cls(
            signed_in=signed_in,
            force=force,
        )

        answer_sign_in_request.additional_properties = d
        return answer_sign_in_request

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
