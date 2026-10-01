from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

T = TypeVar("T", bound="GroupWaitingResponse")


@_attrs_define
class GroupWaitingResponse:
    """
    Attributes:
        asked_at (datetime.datetime):
        notification_id (UUID): Answer it with the notification's respond endpoint.
        question (str):
    """

    asked_at: datetime.datetime
    notification_id: UUID
    question: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        asked_at = self.asked_at.isoformat()

        notification_id = str(self.notification_id)

        question = self.question

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "asked_at": asked_at,
                "notification_id": notification_id,
                "question": question,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        asked_at = isoparse(d.pop("asked_at"))

        notification_id = UUID(d.pop("notification_id"))

        question = d.pop("question")

        group_waiting_response = cls(
            asked_at=asked_at,
            notification_id=notification_id,
            question=question,
        )

        group_waiting_response.additional_properties = d
        return group_waiting_response

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
