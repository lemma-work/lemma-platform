from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="OrganizationEffectivePermissionsResponse")


@_attrs_define
class OrganizationEffectivePermissionsResponse:
    """The organization-level permission ids the caller holds.

    Attributes:
        actions (list[str]):
        organization_id (UUID):
    """

    actions: list[str]
    organization_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        actions = self.actions

        organization_id = str(self.organization_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "actions": actions,
                "organization_id": organization_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        actions = cast(list[str], d.pop("actions"))

        organization_id = UUID(d.pop("organization_id"))

        organization_effective_permissions_response = cls(
            actions=actions,
            organization_id=organization_id,
        )

        organization_effective_permissions_response.additional_properties = d
        return organization_effective_permissions_response

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
