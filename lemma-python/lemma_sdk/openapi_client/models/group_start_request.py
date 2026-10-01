from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="GroupStartRequest")


@_attrs_define
class GroupStartRequest:
    """
    Attributes:
        surface_name (str):
        title (str):
        answers_outsiders (bool | Unset):  Default: True.
    """

    surface_name: str
    title: str
    answers_outsiders: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        surface_name = self.surface_name

        title = self.title

        answers_outsiders = self.answers_outsiders

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "surface_name": surface_name,
                "title": title,
            }
        )
        if answers_outsiders is not UNSET:
            field_dict["answers_outsiders"] = answers_outsiders

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        surface_name = d.pop("surface_name")

        title = d.pop("title")

        answers_outsiders = d.pop("answers_outsiders", UNSET)

        group_start_request = cls(
            surface_name=surface_name,
            title=title,
            answers_outsiders=answers_outsiders,
        )

        group_start_request.additional_properties = d
        return group_start_request

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
