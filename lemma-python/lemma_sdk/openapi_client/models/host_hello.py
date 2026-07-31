from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="HostHello")


@_attrs_define
class HostHello:
    """
    Attributes:
        host_release (str):
        installation_id (str):
        protocol_version (int):
    """

    host_release: str
    installation_id: str
    protocol_version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        host_release = self.host_release

        installation_id = self.installation_id

        protocol_version = self.protocol_version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "host_release": host_release,
                "installation_id": installation_id,
                "protocol_version": protocol_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        host_release = d.pop("host_release")

        installation_id = d.pop("installation_id")

        protocol_version = d.pop("protocol_version")

        host_hello = cls(
            host_release=host_release,
            installation_id=installation_id,
            protocol_version=protocol_version,
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
