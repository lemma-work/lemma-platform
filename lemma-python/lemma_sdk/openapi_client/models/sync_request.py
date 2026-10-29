from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.sync_request_direction import SyncRequestDirection

T = TypeVar("T", bound="SyncRequest")


@_attrs_define
class SyncRequest:
    """
    Attributes:
        direction (SyncRequestDirection):
        machine_path (str):
        pod_id (UUID):
        pod_path (str): `to_pod`: the pod folder to put the files in. `from_pod`: the pod file to copy onto the machine.
    """

    direction: SyncRequestDirection
    machine_path: str
    pod_id: UUID
    pod_path: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        direction = self.direction.value

        machine_path = self.machine_path

        pod_id = str(self.pod_id)

        pod_path = self.pod_path

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "direction": direction,
                "machine_path": machine_path,
                "pod_id": pod_id,
                "pod_path": pod_path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        direction = SyncRequestDirection(d.pop("direction"))

        machine_path = d.pop("machine_path")

        pod_id = UUID(d.pop("pod_id"))

        pod_path = d.pop("pod_path")

        sync_request = cls(
            direction=direction,
            machine_path=machine_path,
            pod_id=pod_id,
            pod_path=pod_path,
        )

        sync_request.additional_properties = d
        return sync_request

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
