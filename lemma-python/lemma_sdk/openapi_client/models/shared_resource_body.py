from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.resource_type import ResourceType

T = TypeVar("T", bound="SharedResourceBody")


@_attrs_define
class SharedResourceBody:
    """
    Attributes:
        permission_ids (list[str]): Reading only: datastore.table.read and datastore.record.read for a table,
            folder.read for a folder.
        resource_name (str): The table's name, or the folder's path.
        resource_type (ResourceType):
    """

    permission_ids: list[str]
    resource_name: str
    resource_type: ResourceType
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        permission_ids = self.permission_ids

        resource_name = self.resource_name

        resource_type = self.resource_type.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "permission_ids": permission_ids,
                "resource_name": resource_name,
                "resource_type": resource_type,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        permission_ids = cast(list[str], d.pop("permission_ids"))

        resource_name = d.pop("resource_name")

        resource_type = ResourceType(d.pop("resource_type"))

        shared_resource_body = cls(
            permission_ids=permission_ids,
            resource_name=resource_name,
            resource_type=resource_type,
        )

        shared_resource_body.additional_properties = d
        return shared_resource_body

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
