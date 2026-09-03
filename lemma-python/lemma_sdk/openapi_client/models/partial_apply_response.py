from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="PartialApplyResponse")


@_attrs_define
class PartialApplyResponse:
    """What a stopped import already wrote to the pod, and how to continue it.

    Apply is not transactional: each step commits in its own unit of work, so an
    import that fails or is cancelled part-way leaves the pod changed and there
    is no rollback. `committed_steps` says which steps landed, but a bare list
    of integers does not tell anyone that the pod was modified at all, nor that
    re-applying resumes instead of duplicating. This does.

        Attributes:
            resumable (bool): Whether applying this import again continues it. False once the job reached a status apply no
                longer accepts, in which case the pod keeps what was already applied and the rest must be imported afresh.
            steps_applied (int): Plan steps already applied to this pod. Not undone.
            steps_total (int): Steps in the approved plan.
            resume_from_step (int | None | Unset): Index of the first step still to run. Applying this import again resumes
                here; steps already applied are not repeated.
    """

    resumable: bool
    steps_applied: int
    steps_total: int
    resume_from_step: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        resumable = self.resumable

        steps_applied = self.steps_applied

        steps_total = self.steps_total

        resume_from_step: int | None | Unset
        if isinstance(self.resume_from_step, Unset):
            resume_from_step = UNSET
        else:
            resume_from_step = self.resume_from_step

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "resumable": resumable,
                "steps_applied": steps_applied,
                "steps_total": steps_total,
            }
        )
        if resume_from_step is not UNSET:
            field_dict["resume_from_step"] = resume_from_step

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        resumable = d.pop("resumable")

        steps_applied = d.pop("steps_applied")

        steps_total = d.pop("steps_total")

        def _parse_resume_from_step(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        resume_from_step = _parse_resume_from_step(d.pop("resume_from_step", UNSET))

        partial_apply_response = cls(
            resumable=resumable,
            steps_applied=steps_applied,
            steps_total=steps_total,
            resume_from_step=resume_from_step,
        )

        partial_apply_response.additional_properties = d
        return partial_apply_response

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
