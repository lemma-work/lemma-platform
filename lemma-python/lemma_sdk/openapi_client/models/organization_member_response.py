from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.organization_role import OrganizationRole
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.user_response import UserResponse


T = TypeVar("T", bound="OrganizationMemberResponse")


@_attrs_define
class OrganizationMemberResponse:
    """Organization member response schema.

    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        organization_id (UUID):
        role (OrganizationRole): Roles for organization membership.
        updated_at (datetime.datetime):
        user_id (UUID):
        user (None | Unset | UserResponse):
    """

    created_at: datetime.datetime
    id: UUID
    organization_id: UUID
    role: OrganizationRole
    updated_at: datetime.datetime
    user_id: UUID
    user: None | Unset | UserResponse = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.user_response import UserResponse

        created_at = self.created_at.isoformat()

        id = str(self.id)

        organization_id = str(self.organization_id)

        role = self.role.value

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        user: dict[str, Any] | None | Unset
        if isinstance(self.user, Unset):
            user = UNSET
        elif isinstance(self.user, UserResponse):
            user = self.user.to_dict()
        else:
            user = self.user

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "organization_id": organization_id,
                "role": role,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if user is not UNSET:
            field_dict["user"] = user

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.user_response import UserResponse

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        organization_id = UUID(d.pop("organization_id"))

        role = OrganizationRole(d.pop("role"))

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        def _parse_user(data: object) -> None | Unset | UserResponse:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                user_type_0 = UserResponse.from_dict(data)

                return user_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UserResponse, data)

        user = _parse_user(d.pop("user", UNSET))

        organization_member_response = cls(
            created_at=created_at,
            id=id,
            organization_id=organization_id,
            role=role,
            updated_at=updated_at,
            user_id=user_id,
            user=user,
        )

        organization_member_response.additional_properties = d
        return organization_member_response

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
