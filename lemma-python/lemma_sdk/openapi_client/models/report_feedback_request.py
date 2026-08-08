from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.feedback_category import FeedbackCategory
from ..types import UNSET, Unset

T = TypeVar("T", bound="ReportFeedbackRequest")


@_attrs_define
class ReportFeedbackRequest:
    """Request payload for maintainer feedback reports.

    Attributes:
        actual_behavior (str): What actually happened.
        category (FeedbackCategory): Which part of Lemma the report is about.
        expected_behavior (str): What the caller expected to happen instead.
        issue_encountered (str): What issue, problem, or incorrect information was encountered.
        subject (str): Short subject line summarizing the report.
        suggested_next_steps (None | str | Unset): Optional proposed fixes, follow-ups, or next steps.
    """

    actual_behavior: str
    category: FeedbackCategory
    expected_behavior: str
    issue_encountered: str
    subject: str
    suggested_next_steps: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        actual_behavior = self.actual_behavior

        category = self.category.value

        expected_behavior = self.expected_behavior

        issue_encountered = self.issue_encountered

        subject = self.subject

        suggested_next_steps: None | str | Unset
        if isinstance(self.suggested_next_steps, Unset):
            suggested_next_steps = UNSET
        else:
            suggested_next_steps = self.suggested_next_steps

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "actual_behavior": actual_behavior,
                "category": category,
                "expected_behavior": expected_behavior,
                "issue_encountered": issue_encountered,
                "subject": subject,
            }
        )
        if suggested_next_steps is not UNSET:
            field_dict["suggested_next_steps"] = suggested_next_steps

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        actual_behavior = d.pop("actual_behavior")

        category = FeedbackCategory(d.pop("category"))

        expected_behavior = d.pop("expected_behavior")

        issue_encountered = d.pop("issue_encountered")

        subject = d.pop("subject")

        def _parse_suggested_next_steps(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        suggested_next_steps = _parse_suggested_next_steps(
            d.pop("suggested_next_steps", UNSET)
        )

        report_feedback_request = cls(
            actual_behavior=actual_behavior,
            category=category,
            expected_behavior=expected_behavior,
            issue_encountered=issue_encountered,
            subject=subject,
            suggested_next_steps=suggested_next_steps,
        )

        report_feedback_request.additional_properties = d
        return report_feedback_request

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
