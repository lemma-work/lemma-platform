from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.measure_status import MeasureStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="ScorecardWeekScore")


@_attrs_define
class ScorecardWeekScore:
    """One measure's result for one week, as `score_week` records it.

    Attributes:
        key (str):
        measure (str):
        shown (str): The one string to show: "31 of 40", "none", "1.6 days".
        status (MeasureStatus):
        target_label (str):
        counted (float | None | Unset):
        met (bool | None | Unset):
        reason (None | str | Unset):
        total (float | None | Unset):
        value (float | None | Unset):
    """

    key: str
    measure: str
    shown: str
    status: MeasureStatus
    target_label: str
    counted: float | None | Unset = UNSET
    met: bool | None | Unset = UNSET
    reason: None | str | Unset = UNSET
    total: float | None | Unset = UNSET
    value: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        measure = self.measure

        shown = self.shown

        status = self.status.value

        target_label = self.target_label

        counted: float | None | Unset
        if isinstance(self.counted, Unset):
            counted = UNSET
        else:
            counted = self.counted

        met: bool | None | Unset
        if isinstance(self.met, Unset):
            met = UNSET
        else:
            met = self.met

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        total: float | None | Unset
        if isinstance(self.total, Unset):
            total = UNSET
        else:
            total = self.total

        value: float | None | Unset
        if isinstance(self.value, Unset):
            value = UNSET
        else:
            value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "key": key,
                "measure": measure,
                "shown": shown,
                "status": status,
                "target_label": target_label,
            }
        )
        if counted is not UNSET:
            field_dict["counted"] = counted
        if met is not UNSET:
            field_dict["met"] = met
        if reason is not UNSET:
            field_dict["reason"] = reason
        if total is not UNSET:
            field_dict["total"] = total
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        key = d.pop("key")

        measure = d.pop("measure")

        shown = d.pop("shown")

        status = MeasureStatus(d.pop("status"))

        target_label = d.pop("target_label")

        def _parse_counted(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        counted = _parse_counted(d.pop("counted", UNSET))

        def _parse_met(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        met = _parse_met(d.pop("met", UNSET))

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        def _parse_total(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        total = _parse_total(d.pop("total", UNSET))

        def _parse_value(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        value = _parse_value(d.pop("value", UNSET))

        scorecard_week_score = cls(
            key=key,
            measure=measure,
            shown=shown,
            status=status,
            target_label=target_label,
            counted=counted,
            met=met,
            reason=reason,
            total=total,
            value=value,
        )

        scorecard_week_score.additional_properties = d
        return scorecard_week_score

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
