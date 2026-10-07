from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.scorecard_measure_spec import ScorecardMeasureSpec


T = TypeVar("T", bound="ScorecardPreviewRequest")


@_attrs_define
class ScorecardPreviewRequest:
    """
    Attributes:
        end (datetime.date | None | Unset): The day the last week ends, not included. Defaults to today; cannot be after
            it.
        keys (list[str] | None | Unset): Saved measures to preview, by key, whether on or off.
        measure (None | ScorecardMeasureSpec | Unset): One unsaved measure to preview instead. Nothing is saved.
        weeks (int | Unset): How many weeks to count, oldest first. Default: 4.
    """

    end: datetime.date | None | Unset = UNSET
    keys: list[str] | None | Unset = UNSET
    measure: None | ScorecardMeasureSpec | Unset = UNSET
    weeks: int | Unset = 4
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.scorecard_measure_spec import ScorecardMeasureSpec

        end: None | str | Unset
        if isinstance(self.end, Unset):
            end = UNSET
        elif isinstance(self.end, datetime.date):
            end = self.end.isoformat()
        else:
            end = self.end

        keys: list[str] | None | Unset
        if isinstance(self.keys, Unset):
            keys = UNSET
        elif isinstance(self.keys, list):
            keys = self.keys

        else:
            keys = self.keys

        measure: dict[str, Any] | None | Unset
        if isinstance(self.measure, Unset):
            measure = UNSET
        elif isinstance(self.measure, ScorecardMeasureSpec):
            measure = self.measure.to_dict()
        else:
            measure = self.measure

        weeks = self.weeks

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if end is not UNSET:
            field_dict["end"] = end
        if keys is not UNSET:
            field_dict["keys"] = keys
        if measure is not UNSET:
            field_dict["measure"] = measure
        if weeks is not UNSET:
            field_dict["weeks"] = weeks

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.scorecard_measure_spec import ScorecardMeasureSpec

        d = dict(src_dict)

        def _parse_end(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                end_type_0 = isoparse(data).date()

                return end_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.date | None | Unset, data)

        end = _parse_end(d.pop("end", UNSET))

        def _parse_keys(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                keys_type_0 = cast(list[str], data)

                return keys_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        keys = _parse_keys(d.pop("keys", UNSET))

        def _parse_measure(data: object) -> None | ScorecardMeasureSpec | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                measure_type_0 = ScorecardMeasureSpec.from_dict(data)

                return measure_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | ScorecardMeasureSpec | Unset, data)

        measure = _parse_measure(d.pop("measure", UNSET))

        weeks = d.pop("weeks", UNSET)

        scorecard_preview_request = cls(
            end=end,
            keys=keys,
            measure=measure,
            weeks=weeks,
        )

        scorecard_preview_request.additional_properties = d
        return scorecard_preview_request

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
