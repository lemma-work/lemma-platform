from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ImpactedPod")


@_attrs_define
class ImpactedPod:
    """One pod the caller can open, and what in it stops.

    Attributes:
        name (str):
        pod_id (UUID):
        event_subscription_count (int | Unset):  Default: 0.
        pod_connectors (list[str] | Unset): Slugs of its connectors on it.
        schedule_count (int | Unset):  Default: 0.
        surface_count (int | Unset):  Default: 0.
    """

    name: str
    pod_id: UUID
    event_subscription_count: int | Unset = 0
    pod_connectors: list[str] | Unset = UNSET
    schedule_count: int | Unset = 0
    surface_count: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        pod_id = str(self.pod_id)

        event_subscription_count = self.event_subscription_count

        pod_connectors: list[str] | Unset = UNSET
        if not isinstance(self.pod_connectors, Unset):
            pod_connectors = self.pod_connectors

        schedule_count = self.schedule_count

        surface_count = self.surface_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "pod_id": pod_id,
            }
        )
        if event_subscription_count is not UNSET:
            field_dict["event_subscription_count"] = event_subscription_count
        if pod_connectors is not UNSET:
            field_dict["pod_connectors"] = pod_connectors
        if schedule_count is not UNSET:
            field_dict["schedule_count"] = schedule_count
        if surface_count is not UNSET:
            field_dict["surface_count"] = surface_count

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        event_subscription_count = d.pop("event_subscription_count", UNSET)

        pod_connectors = cast(list[str], d.pop("pod_connectors", UNSET))

        schedule_count = d.pop("schedule_count", UNSET)

        surface_count = d.pop("surface_count", UNSET)

        impacted_pod = cls(
            name=name,
            pod_id=pod_id,
            event_subscription_count=event_subscription_count,
            pod_connectors=pod_connectors,
            schedule_count=schedule_count,
            surface_count=surface_count,
        )

        impacted_pod.additional_properties = d
        return impacted_pod

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
