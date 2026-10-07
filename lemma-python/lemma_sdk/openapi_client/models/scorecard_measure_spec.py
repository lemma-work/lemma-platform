from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.aim import Aim
from ..models.shape import Shape
from ..types import UNSET, Unset

T = TypeVar("T", bound="ScorecardMeasureSpec")


@_attrs_define
class ScorecardMeasureSpec:
    """A measure as a `scorecard` row holds it, not yet saved.

    Other columns of a row (`kind`, `is_on`, `note`, ...) are accepted and play
    no part in counting it.

        Attributes:
            aim (Aim):
            target (float):
            counter (str | Unset): `work`, `sql`, `approvals`, `standing_work` or `open_questions`. Default: 'work'.
            key (str | Unset): The row's key. Default: 'draft'.
            label_column (None | str | Unset): The column naming a row when it is listed.
            link_column (None | str | Unset): The column holding a row's link.
            measure (str | Unset): The measure in one sentence. Default: ''.
            query (None | str | Unset): `sql` counter only: a SELECT returning `counted` and `total`.
            shape (None | Shape | Unset): `share`, `count`, `median` or `total`. Omitted: a share when `aim` is higher, a
                count when it is lower.
            target_label (str | Unset):  Default: ''.
            test (None | str | Unset): One SQL boolean expression over a row.
            time_column (None | str | Unset): The DATE or DATETIME column that places a row in a week.
            unit_table (None | str | Unset): The table with one row per unit of work.
            value (None | str | Unset): Median or total: one SQL number over a row.
            value_unit (None | str | Unset): What a count, median or total is in: `minutes`.
    """

    aim: Aim
    target: float
    counter: str | Unset = "work"
    key: str | Unset = "draft"
    label_column: None | str | Unset = UNSET
    link_column: None | str | Unset = UNSET
    measure: str | Unset = ""
    query: None | str | Unset = UNSET
    shape: None | Shape | Unset = UNSET
    target_label: str | Unset = ""
    test: None | str | Unset = UNSET
    time_column: None | str | Unset = UNSET
    unit_table: None | str | Unset = UNSET
    value: None | str | Unset = UNSET
    value_unit: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        aim = self.aim.value

        target = self.target

        counter = self.counter

        key = self.key

        label_column: None | str | Unset
        if isinstance(self.label_column, Unset):
            label_column = UNSET
        else:
            label_column = self.label_column

        link_column: None | str | Unset
        if isinstance(self.link_column, Unset):
            link_column = UNSET
        else:
            link_column = self.link_column

        measure = self.measure

        query: None | str | Unset
        if isinstance(self.query, Unset):
            query = UNSET
        else:
            query = self.query

        shape: None | str | Unset
        if isinstance(self.shape, Unset):
            shape = UNSET
        elif isinstance(self.shape, Shape):
            shape = self.shape.value
        else:
            shape = self.shape

        target_label = self.target_label

        test: None | str | Unset
        if isinstance(self.test, Unset):
            test = UNSET
        else:
            test = self.test

        time_column: None | str | Unset
        if isinstance(self.time_column, Unset):
            time_column = UNSET
        else:
            time_column = self.time_column

        unit_table: None | str | Unset
        if isinstance(self.unit_table, Unset):
            unit_table = UNSET
        else:
            unit_table = self.unit_table

        value: None | str | Unset
        if isinstance(self.value, Unset):
            value = UNSET
        else:
            value = self.value

        value_unit: None | str | Unset
        if isinstance(self.value_unit, Unset):
            value_unit = UNSET
        else:
            value_unit = self.value_unit

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "aim": aim,
                "target": target,
            }
        )
        if counter is not UNSET:
            field_dict["counter"] = counter
        if key is not UNSET:
            field_dict["key"] = key
        if label_column is not UNSET:
            field_dict["label_column"] = label_column
        if link_column is not UNSET:
            field_dict["link_column"] = link_column
        if measure is not UNSET:
            field_dict["measure"] = measure
        if query is not UNSET:
            field_dict["query"] = query
        if shape is not UNSET:
            field_dict["shape"] = shape
        if target_label is not UNSET:
            field_dict["target_label"] = target_label
        if test is not UNSET:
            field_dict["test"] = test
        if time_column is not UNSET:
            field_dict["time_column"] = time_column
        if unit_table is not UNSET:
            field_dict["unit_table"] = unit_table
        if value is not UNSET:
            field_dict["value"] = value
        if value_unit is not UNSET:
            field_dict["value_unit"] = value_unit

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        aim = Aim(d.pop("aim"))

        target = d.pop("target")

        counter = d.pop("counter", UNSET)

        key = d.pop("key", UNSET)

        def _parse_label_column(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label_column = _parse_label_column(d.pop("label_column", UNSET))

        def _parse_link_column(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        link_column = _parse_link_column(d.pop("link_column", UNSET))

        measure = d.pop("measure", UNSET)

        def _parse_query(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        query = _parse_query(d.pop("query", UNSET))

        def _parse_shape(data: object) -> None | Shape | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                shape_type_0 = Shape(data)

                return shape_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Shape | Unset, data)

        shape = _parse_shape(d.pop("shape", UNSET))

        target_label = d.pop("target_label", UNSET)

        def _parse_test(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        test = _parse_test(d.pop("test", UNSET))

        def _parse_time_column(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        time_column = _parse_time_column(d.pop("time_column", UNSET))

        def _parse_unit_table(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        unit_table = _parse_unit_table(d.pop("unit_table", UNSET))

        def _parse_value(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        value = _parse_value(d.pop("value", UNSET))

        def _parse_value_unit(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        value_unit = _parse_value_unit(d.pop("value_unit", UNSET))

        scorecard_measure_spec = cls(
            aim=aim,
            target=target,
            counter=counter,
            key=key,
            label_column=label_column,
            link_column=link_column,
            measure=measure,
            query=query,
            shape=shape,
            target_label=target_label,
            test=test,
            time_column=time_column,
            unit_table=unit_table,
            value=value,
            value_unit=value_unit,
        )

        scorecard_measure_spec.additional_properties = d
        return scorecard_measure_spec

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
