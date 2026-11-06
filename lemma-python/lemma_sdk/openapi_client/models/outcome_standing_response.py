from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.pod_outcome import PodOutcome


T = TypeVar("T", bound="OutcomeStandingResponse")


@_attrs_define
class OutcomeStandingResponse:
    """
    Attributes:
        outcome (None | PodOutcome): What the pod owns, or null when it owns nothing yet.
        measured_at (datetime.datetime | None | Unset):
        problem (None | str | Unset): Why the measure could not be read, in plain words.
        value (float | None | Unset): What its measure answers now, counted over every row for everyone in the pod
            alike. Null with no measure, when it answers null, or when it could not be read (see `problem`).
    """

    outcome: None | PodOutcome
    measured_at: datetime.datetime | None | Unset = UNSET
    problem: None | str | Unset = UNSET
    value: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.pod_outcome import PodOutcome

        outcome: dict[str, Any] | None
        if isinstance(self.outcome, PodOutcome):
            outcome = self.outcome.to_dict()
        else:
            outcome = self.outcome

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

        value: float | None | Unset
        if isinstance(self.value, Unset):
            value = UNSET
        else:
            value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "outcome": outcome,
            }
        )
        if measured_at is not UNSET:
            field_dict["measured_at"] = measured_at
        if problem is not UNSET:
            field_dict["problem"] = problem
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pod_outcome import PodOutcome

        d = dict(src_dict)

        def _parse_outcome(data: object) -> None | PodOutcome:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                outcome_type_0 = PodOutcome.from_dict(data)

                return outcome_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodOutcome, data)

        outcome = _parse_outcome(d.pop("outcome"))

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

        def _parse_value(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        value = _parse_value(d.pop("value", UNSET))

        outcome_standing_response = cls(
            outcome=outcome,
            measured_at=measured_at,
            problem=problem,
            value=value,
        )

        outcome_standing_response.additional_properties = d
        return outcome_standing_response

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
