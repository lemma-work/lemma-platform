from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ScorecardUnitRow")


@_attrs_define
class ScorecardUnitRow:
    """
    Attributes:
        id (str):
        at (None | str | Unset): The time column's value, ISO 8601.
        label (None | str | Unset):
        link (None | str | Unset):
        passed (bool | None | Unset): Whether the number counted this row.
    """

    id: str
    at: None | str | Unset = UNSET
    label: None | str | Unset = UNSET
    link: None | str | Unset = UNSET
    passed: bool | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        at: None | str | Unset
        if isinstance(self.at, Unset):
            at = UNSET
        else:
            at = self.at

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        link: None | str | Unset
        if isinstance(self.link, Unset):
            link = UNSET
        else:
            link = self.link

        passed: bool | None | Unset
        if isinstance(self.passed, Unset):
            passed = UNSET
        else:
            passed = self.passed

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
            }
        )
        if at is not UNSET:
            field_dict["at"] = at
        if label is not UNSET:
            field_dict["label"] = label
        if link is not UNSET:
            field_dict["link"] = link
        if passed is not UNSET:
            field_dict["passed"] = passed

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        def _parse_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        at = _parse_at(d.pop("at", UNSET))

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        def _parse_link(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        link = _parse_link(d.pop("link", UNSET))

        def _parse_passed(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        passed = _parse_passed(d.pop("passed", UNSET))

        scorecard_unit_row = cls(
            id=id,
            at=at,
            label=label,
            link=link,
            passed=passed,
        )

        scorecard_unit_row.additional_properties = d
        return scorecard_unit_row

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
