from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

if TYPE_CHECKING:
    from ..models.pod_recents_response import PodRecentsResponse


T = TypeVar("T", bound="OrganizationRecentsResponse")


@_attrs_define
class OrganizationRecentsResponse:
    """
    Attributes:
        days (int):
        organization_id (UUID):
        pods (list[PodRecentsResponse]):
        since (datetime.datetime): When the window's first day began.
        timezone (str):
    """

    days: int
    organization_id: UUID
    pods: list[PodRecentsResponse]
    since: datetime.datetime
    timezone: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        days = self.days

        organization_id = str(self.organization_id)

        pods = []
        for pods_item_data in self.pods:
            pods_item = pods_item_data.to_dict()
            pods.append(pods_item)

        since = self.since.isoformat()

        timezone = self.timezone

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "days": days,
                "organization_id": organization_id,
                "pods": pods,
                "since": since,
                "timezone": timezone,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pod_recents_response import PodRecentsResponse

        d = dict(src_dict)
        days = d.pop("days")

        organization_id = UUID(d.pop("organization_id"))

        pods = []
        _pods = d.pop("pods")
        for pods_item_data in _pods:
            pods_item = PodRecentsResponse.from_dict(pods_item_data)

            pods.append(pods_item)

        since = isoparse(d.pop("since"))

        timezone = d.pop("timezone")

        organization_recents_response = cls(
            days=days,
            organization_id=organization_id,
            pods=pods,
            since=since,
            timezone=timezone,
        )

        organization_recents_response.additional_properties = d
        return organization_recents_response

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
