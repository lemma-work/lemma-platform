from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.resource_type import ResourceType
from ..types import UNSET, Unset

T = TypeVar("T", bound="ResourceAccessGrantResponse")


@_attrs_define
class ResourceAccessGrantResponse:
    """
    Attributes:
        grantee_id (UUID):
        grantee_type (str):
        resource_name (str):
        resource_type (ResourceType):
        display_name (None | str | Unset):
        email (None | str | Unset):
        permission_ids (list[str] | Unset):
        role_name (None | str | Unset):
        user_id (None | Unset | UUID):
    """

    grantee_id: UUID
    grantee_type: str
    resource_name: str
    resource_type: ResourceType
    display_name: None | str | Unset = UNSET
    email: None | str | Unset = UNSET
    permission_ids: list[str] | Unset = UNSET
    role_name: None | str | Unset = UNSET
    user_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        grantee_id = str(self.grantee_id)

        grantee_type = self.grantee_type

        resource_name = self.resource_name

        resource_type = self.resource_type.value

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        email: None | str | Unset
        if isinstance(self.email, Unset):
            email = UNSET
        else:
            email = self.email

        permission_ids: list[str] | Unset = UNSET
        if not isinstance(self.permission_ids, Unset):
            permission_ids = self.permission_ids

        role_name: None | str | Unset
        if isinstance(self.role_name, Unset):
            role_name = UNSET
        else:
            role_name = self.role_name

        user_id: None | str | Unset
        if isinstance(self.user_id, Unset):
            user_id = UNSET
        elif isinstance(self.user_id, UUID):
            user_id = str(self.user_id)
        else:
            user_id = self.user_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "grantee_id": grantee_id,
                "grantee_type": grantee_type,
                "resource_name": resource_name,
                "resource_type": resource_type,
            }
        )
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if email is not UNSET:
            field_dict["email"] = email
        if permission_ids is not UNSET:
            field_dict["permission_ids"] = permission_ids
        if role_name is not UNSET:
            field_dict["role_name"] = role_name
        if user_id is not UNSET:
            field_dict["user_id"] = user_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        grantee_id = UUID(d.pop("grantee_id"))

        grantee_type = d.pop("grantee_type")

        resource_name = d.pop("resource_name")

        resource_type = ResourceType(d.pop("resource_type"))

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        def _parse_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email = _parse_email(d.pop("email", UNSET))

        permission_ids = cast(list[str], d.pop("permission_ids", UNSET))

        def _parse_role_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        role_name = _parse_role_name(d.pop("role_name", UNSET))

        def _parse_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                user_id_type_0 = UUID(data)

                return user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        user_id = _parse_user_id(d.pop("user_id", UNSET))

        resource_access_grant_response = cls(
            grantee_id=grantee_id,
            grantee_type=grantee_type,
            resource_name=resource_name,
            resource_type=resource_type,
            display_name=display_name,
            email=email,
            permission_ids=permission_ids,
            role_name=role_name,
            user_id=user_id,
        )

        resource_access_grant_response.additional_properties = d
        return resource_access_grant_response

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
