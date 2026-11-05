from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="TemplatePlan")


@_attrs_define
class TemplatePlan:
    """The first lines of the plan a Lemma made from this template starts with.

    Attributes:
        how (list[str] | Unset):
        never (list[str] | Unset):
    """

    how: list[str] | Unset = UNSET
    never: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        how: list[str] | Unset = UNSET
        if not isinstance(self.how, Unset):
            how = self.how

        never: list[str] | Unset = UNSET
        if not isinstance(self.never, Unset):
            never = self.never

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if how is not UNSET:
            field_dict["how"] = how
        if never is not UNSET:
            field_dict["never"] = never

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        how = cast(list[str], d.pop("how", UNSET))

        never = cast(list[str], d.pop("never", UNSET))

        template_plan = cls(
            how=how,
            never=never,
        )

        template_plan.additional_properties = d
        return template_plan

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
