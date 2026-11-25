from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.public_audience import PublicAudience
from ..types import UNSET, Unset

T = TypeVar("T", bound="OpenTableRequest")


@_attrs_define
class OpenTableRequest:
    """
    Attributes:
        audience (PublicAudience): Who outside the pod may add rows.
        columns (list[str]): The columns people outside may fill, in the order to ask them. Checked against the table
            when it is opened.
        answers_per_day (int | None | Unset): How many answers a day it takes, up to `answers_per_day_max`. Omitted, the
            deployment's default. Opening again replaces it.
    """

    audience: PublicAudience
    columns: list[str]
    answers_per_day: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        audience = self.audience.value

        columns = self.columns

        answers_per_day: int | None | Unset
        if isinstance(self.answers_per_day, Unset):
            answers_per_day = UNSET
        else:
            answers_per_day = self.answers_per_day

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "audience": audience,
                "columns": columns,
            }
        )
        if answers_per_day is not UNSET:
            field_dict["answers_per_day"] = answers_per_day

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        audience = PublicAudience(d.pop("audience"))

        columns = cast(list[str], d.pop("columns"))

        def _parse_answers_per_day(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        answers_per_day = _parse_answers_per_day(d.pop("answers_per_day", UNSET))

        open_table_request = cls(
            audience=audience,
            columns=columns,
            answers_per_day=answers_per_day,
        )

        open_table_request.additional_properties = d
        return open_table_request

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
