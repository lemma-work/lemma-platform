from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="HostHello")


@_attrs_define
class HostHello:
    """
    Attributes:
        adapter_manifest_id (str):
        host_release (str):
        installation_id (str):
        instance_id (UUID):
        protocol_max (int):
        protocol_min (int):
    """

    adapter_manifest_id: str
    host_release: str
    installation_id: str
    instance_id: UUID
    protocol_max: int
    protocol_min: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        adapter_manifest_id = self.adapter_manifest_id

        host_release = self.host_release

        installation_id = self.installation_id

        instance_id = str(self.instance_id)

        protocol_max = self.protocol_max

        protocol_min = self.protocol_min

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "adapter_manifest_id": adapter_manifest_id,
                "host_release": host_release,
                "installation_id": installation_id,
                "instance_id": instance_id,
                "protocol_max": protocol_max,
                "protocol_min": protocol_min,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        adapter_manifest_id = d.pop("adapter_manifest_id")

        host_release = d.pop("host_release")

        installation_id = d.pop("installation_id")

        instance_id = UUID(d.pop("instance_id"))

        protocol_max = d.pop("protocol_max")

        protocol_min = d.pop("protocol_min")

        host_hello = cls(
            adapter_manifest_id=adapter_manifest_id,
            host_release=host_release,
            installation_id=installation_id,
            instance_id=instance_id,
            protocol_max=protocol_max,
            protocol_min=protocol_min,
        )

        host_hello.additional_properties = d
        return host_hello

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
