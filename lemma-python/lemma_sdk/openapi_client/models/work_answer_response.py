from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.job import Job
from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkAnswerResponse")


@_attrs_define
class WorkAnswerResponse:
    """What the person said they do, and when.

    Attributes:
        answered_at (datetime.datetime):
        jobs (list[Job]):
        other (None | str | Unset):
    """

    answered_at: datetime.datetime
    jobs: list[Job]
    other: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        answered_at = self.answered_at.isoformat()

        jobs = []
        for jobs_item_data in self.jobs:
            jobs_item = jobs_item_data.value
            jobs.append(jobs_item)

        other: None | str | Unset
        if isinstance(self.other, Unset):
            other = UNSET
        else:
            other = self.other

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "answered_at": answered_at,
                "jobs": jobs,
            }
        )
        if other is not UNSET:
            field_dict["other"] = other

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        answered_at = isoparse(d.pop("answered_at"))

        jobs = []
        _jobs = d.pop("jobs")
        for jobs_item_data in _jobs:
            jobs_item = Job(jobs_item_data)

            jobs.append(jobs_item)

        def _parse_other(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        other = _parse_other(d.pop("other", UNSET))

        work_answer_response = cls(
            answered_at=answered_at,
            jobs=jobs,
            other=other,
        )

        work_answer_response.additional_properties = d
        return work_answer_response

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
