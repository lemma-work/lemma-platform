from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="OutcomeWeekResponse")


@_attrs_define
class OutcomeWeekResponse:
    """
    Attributes:
        starts_on (datetime.date): The Monday the week starts on (UTC).
        measured_at (datetime.datetime | None | Unset): When `value` was read; null when it never was.
        problem (None | str | Unset): Why its last reading could not be taken.
        target (float | None | Unset): The target when the week was read.
        value (float | None | Unset): The week's last reading. Null for a week it was not read in, or that answered
            null.
    """

    starts_on: datetime.date
    measured_at: datetime.datetime | None | Unset = UNSET
    problem: None | str | Unset = UNSET
    target: float | None | Unset = UNSET
    value: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        starts_on = self.starts_on.isoformat()

        measured_at: None | str | Unset
        if isinstance(self.measured_at, Unset):
            measured_at = UNSET
        elif isinstance(self.measured_at, datetime.datetime):
            measured_at = self.measured_at.isoformat()
        else:
            measured_at = self.measured_at

        problem: None | str | Unset
        if isinstance(self.problem, Unset):
            problem = UNSET
        else:
            problem = self.problem

        target: float | None | Unset
        if isinstance(self.target, Unset):
            target = UNSET
        else:
            target = self.target

        value: float | None | Unset
        if isinstance(self.value, Unset):
            value = UNSET
        else:
            value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "starts_on": starts_on,
            }
        )
        if measured_at is not UNSET:
            field_dict["measured_at"] = measured_at
        if problem is not UNSET:
            field_dict["problem"] = problem
        if target is not UNSET:
            field_dict["target"] = target
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        starts_on = isoparse(d.pop("starts_on")).date()

        def _parse_measured_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                measured_at_type_0 = isoparse(data)

                return measured_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        measured_at = _parse_measured_at(d.pop("measured_at", UNSET))

        def _parse_problem(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        problem = _parse_problem(d.pop("problem", UNSET))

        def _parse_target(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        target = _parse_target(d.pop("target", UNSET))

        def _parse_value(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        value = _parse_value(d.pop("value", UNSET))

        outcome_week_response = cls(
            starts_on=starts_on,
            measured_at=measured_at,
            problem=problem,
            target=target,
            value=value,
        )

        outcome_week_response.additional_properties = d
        return outcome_week_response

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
