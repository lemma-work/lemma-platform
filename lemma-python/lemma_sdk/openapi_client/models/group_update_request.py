from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="GroupUpdateRequest")


@_attrs_define
class GroupUpdateRequest:
    """
    Attributes:
        answers_outsiders (bool | None | Unset): Answer people outside the pod in this group. Switching it on where
            nobody answers for them makes the caller the one who does.
        take_over (bool | Unset): The caller answers for this group's outsiders from now on. Default: False.
    """

    answers_outsiders: bool | None | Unset = UNSET
    take_over: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        answers_outsiders: bool | None | Unset
        if isinstance(self.answers_outsiders, Unset):
            answers_outsiders = UNSET
        else:
            answers_outsiders = self.answers_outsiders

        take_over = self.take_over

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if answers_outsiders is not UNSET:
            field_dict["answers_outsiders"] = answers_outsiders
        if take_over is not UNSET:
            field_dict["take_over"] = take_over

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_answers_outsiders(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        answers_outsiders = _parse_answers_outsiders(d.pop("answers_outsiders", UNSET))

        take_over = d.pop("take_over", UNSET)

        group_update_request = cls(
            answers_outsiders=answers_outsiders,
            take_over=take_over,
        )

        group_update_request.additional_properties = d
        return group_update_request

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
