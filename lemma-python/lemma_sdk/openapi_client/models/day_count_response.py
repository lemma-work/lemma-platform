from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

T = TypeVar("T", bound="DayCountResponse")


@_attrs_define
class DayCountResponse:
    """
    Attributes:
        by_agent (int): Conversations last active that day that nobody typed the first message of: a schedule, a
            workflow, a notification, a task, or another pod's ask.
        by_people (int): Conversations a person started.
        day (datetime.date): A calendar day in the requested time zone.
    """

    by_agent: int
    by_people: int
    day: datetime.date
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        by_agent = self.by_agent

        by_people = self.by_people

        day = self.day.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "by_agent": by_agent,
                "by_people": by_people,
                "day": day,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        by_agent = d.pop("by_agent")

        by_people = d.pop("by_people")

        day = isoparse(d.pop("day")).date()

        day_count_response = cls(
            by_agent=by_agent,
            by_people=by_people,
            day=day,
        )

        day_count_response.additional_properties = d
        return day_count_response

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
