from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.trigger_skip_reason import TriggerSkipReason

T = TypeVar("T", bound="TriggerSkipResponse")


@_attrs_define
class TriggerSkipResponse:
    """Changes or fires the trigger let pass without firing, aggregated.

    One row stands for every change of one delivered page skipped for one
    reason: a bulk write of thousands of rows is a handful of rows here.

        Attributes:
            at (datetime.datetime):
            count (int): How many changes or fires this row stands for.
            first (str): Position of the first skipped change or fire.
            id (UUID):
            last (str): Position of the last skipped change or fire.
            reason (TriggerSkipReason):
    """

    at: datetime.datetime
    count: int
    first: str
    id: UUID
    last: str
    reason: TriggerSkipReason
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        at = self.at.isoformat()

        count = self.count

        first = self.first

        id = str(self.id)

        last = self.last

        reason = self.reason.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "at": at,
                "count": count,
                "first": first,
                "id": id,
                "last": last,
                "reason": reason,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        at = isoparse(d.pop("at"))

        count = d.pop("count")

        first = d.pop("first")

        id = UUID(d.pop("id"))

        last = d.pop("last")

        reason = TriggerSkipReason(d.pop("reason"))

        trigger_skip_response = cls(
            at=at,
            count=count,
            first=first,
            id=id,
            last=last,
            reason=reason,
        )

        trigger_skip_response.additional_properties = d
        return trigger_skip_response

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
