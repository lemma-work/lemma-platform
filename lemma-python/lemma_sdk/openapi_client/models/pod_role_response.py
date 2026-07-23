from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="PodRoleResponse")


@_attrs_define
class PodRoleResponse:
    """Pod role response.

    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        is_system (bool):
        name (str):
        pod_id (UUID):
        created_by_user_id (None | Unset | UUID):
        description (None | str | Unset):
        organization_id (None | Unset | UUID):
        permission_ids (list[str] | Unset):
    """

    created_at: datetime.datetime
    id: UUID
    is_system: bool
    name: str
    pod_id: UUID
    created_by_user_id: None | Unset | UUID = UNSET
    description: None | str | Unset = UNSET
    organization_id: None | Unset | UUID = UNSET
    permission_ids: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        is_system = self.is_system

        name = self.name

        pod_id = str(self.pod_id)

        created_by_user_id: None | str | Unset
        if isinstance(self.created_by_user_id, Unset):
            created_by_user_id = UNSET
        elif isinstance(self.created_by_user_id, UUID):
            created_by_user_id = str(self.created_by_user_id)
        else:
            created_by_user_id = self.created_by_user_id

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        organization_id: None | str | Unset
        if isinstance(self.organization_id, Unset):
            organization_id = UNSET
        elif isinstance(self.organization_id, UUID):
            organization_id = str(self.organization_id)
        else:
            organization_id = self.organization_id

        permission_ids: list[str] | Unset = UNSET
        if not isinstance(self.permission_ids, Unset):
            permission_ids = self.permission_ids

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "is_system": is_system,
                "name": name,
                "pod_id": pod_id,
            }
        )
        if created_by_user_id is not UNSET:
            field_dict["created_by_user_id"] = created_by_user_id
        if description is not UNSET:
            field_dict["description"] = description
        if organization_id is not UNSET:
            field_dict["organization_id"] = organization_id
        if permission_ids is not UNSET:
            field_dict["permission_ids"] = permission_ids

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        is_system = d.pop("is_system")

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        def _parse_created_by_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_by_user_id_type_0 = UUID(data)

                return created_by_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        created_by_user_id = _parse_created_by_user_id(
            d.pop("created_by_user_id", UNSET)
        )

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_organization_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                organization_id_type_0 = UUID(data)

                return organization_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        organization_id = _parse_organization_id(d.pop("organization_id", UNSET))

        permission_ids = cast(list[str], d.pop("permission_ids", UNSET))

        pod_role_response = cls(
            created_at=created_at,
            id=id,
            is_system=is_system,
            name=name,
            pod_id=pod_id,
            created_by_user_id=created_by_user_id,
            description=description,
            organization_id=organization_id,
            permission_ids=permission_ids,
        )

        pod_role_response.additional_properties = d
        return pod_role_response

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
