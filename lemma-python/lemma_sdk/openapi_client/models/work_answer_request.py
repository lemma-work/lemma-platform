from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.job import Job
from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkAnswerRequest")


@_attrs_define
class WorkAnswerRequest:
    """What the person does, in answer to the one time they are asked.

    Attributes:
        jobs (list[Job]): What they do, any number of them, each kept once in the order given. Empty with no `other`
            records that they skipped the question, which still stops it being asked again.
        other (None | str | Unset): Something else, in their words. Trimmed; empty is no answer. At most 120 characters
            once trimmed.
    """

    jobs: list[Job]
    other: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
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
                "jobs": jobs,
            }
        )
        if other is not UNSET:
            field_dict["other"] = other

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
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

        work_answer_request = cls(
            jobs=jobs,
            other=other,
        )

        work_answer_request.additional_properties = d
        return work_answer_request

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
