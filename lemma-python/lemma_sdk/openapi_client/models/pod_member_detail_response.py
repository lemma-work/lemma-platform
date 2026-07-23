from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.user_response import UserResponse


T = TypeVar("T", bound="PodMemberDetailResponse")


@_attrs_define
class PodMemberDetailResponse:
    """Pod member detail response schema.

    Attributes:
        created_at (datetime.datetime):
        email (str):
        pod_member_id (UUID):
        updated_at (datetime.datetime):
        user_email (str):
        user_id (UUID):
        roles (list[str] | Unset):
        user (None | Unset | UserResponse):
        user_name (None | str | Unset):
    """

    created_at: datetime.datetime
    email: str
    pod_member_id: UUID
    updated_at: datetime.datetime
    user_email: str
    user_id: UUID
    roles: list[str] | Unset = UNSET
    user: None | Unset | UserResponse = UNSET
    user_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.user_response import UserResponse

        created_at = self.created_at.isoformat()

        email = self.email

        pod_member_id = str(self.pod_member_id)

        updated_at = self.updated_at.isoformat()

        user_email = self.user_email

        user_id = str(self.user_id)

        roles: list[str] | Unset = UNSET
        if not isinstance(self.roles, Unset):
            roles = self.roles

        user: dict[str, Any] | None | Unset
        if isinstance(self.user, Unset):
            user = UNSET
        elif isinstance(self.user, UserResponse):
            user = self.user.to_dict()
        else:
            user = self.user

        user_name: None | str | Unset
        if isinstance(self.user_name, Unset):
            user_name = UNSET
        else:
            user_name = self.user_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "email": email,
                "pod_member_id": pod_member_id,
                "updated_at": updated_at,
                "user_email": user_email,
                "user_id": user_id,
            }
        )
        if roles is not UNSET:
            field_dict["roles"] = roles
        if user is not UNSET:
            field_dict["user"] = user
        if user_name is not UNSET:
            field_dict["user_name"] = user_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.user_response import UserResponse

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        email = d.pop("email")

        pod_member_id = UUID(d.pop("pod_member_id"))

        updated_at = isoparse(d.pop("updated_at"))

        user_email = d.pop("user_email")

        user_id = UUID(d.pop("user_id"))

        roles = cast(list[str], d.pop("roles", UNSET))

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

        def _parse_user_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        user_name = _parse_user_name(d.pop("user_name", UNSET))

        pod_member_detail_response = cls(
            created_at=created_at,
            email=email,
            pod_member_id=pod_member_id,
            updated_at=updated_at,
            user_email=user_email,
            user_id=user_id,
            roles=roles,
            user=user,
            user_name=user_name,
        )

        pod_member_detail_response.additional_properties = d
        return pod_member_detail_response

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
