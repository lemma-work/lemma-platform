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


T = TypeVar("T", bound="OutcomeSetResponse")


@_attrs_define
class OutcomeSetResponse:
    """
    Attributes:
        outcome (PodOutcome): What the pod owns, as stored on its config.
        measured_at (datetime.datetime | None | Unset): When `value` was read; null when it was not.
        value (float | None | Unset): What a new or changed measure answered when it was checked; null when it answered
            null, or was not read because it did not change.
    """

    outcome: PodOutcome
    measured_at: datetime.datetime | None | Unset = UNSET
    value: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        outcome = self.outcome.to_dict()

        measured_at: None | str | Unset
        if isinstance(self.measured_at, Unset):
            measured_at = UNSET
        elif isinstance(self.measured_at, datetime.datetime):
            measured_at = self.measured_at.isoformat()
        else:
            measured_at = self.measured_at

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
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pod_outcome import PodOutcome

        d = dict(src_dict)
        outcome = PodOutcome.from_dict(d.pop("outcome"))

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

        def _parse_value(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        value = _parse_value(d.pop("value", UNSET))

        outcome_set_response = cls(
            outcome=outcome,
            measured_at=measured_at,
            value=value,
        )

        outcome_set_response.additional_properties = d
        return outcome_set_response

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
