from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceConnectionOwner")


@_attrs_define
class SurfaceConnectionOwner:
    """The person whose connected account backs a surface.

    Attributes:
        user_id (UUID):
        email (None | str | Unset):
        is_pod_member (bool | Unset):  Default: False.
        is_you (bool | Unset):  Default: False.
        name (None | str | Unset):
    """

    user_id: UUID
    email: None | str | Unset = UNSET
    is_pod_member: bool | Unset = False
    is_you: bool | Unset = False
    name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        user_id = str(self.user_id)

        email: None | str | Unset
        if isinstance(self.email, Unset):
            email = UNSET
        else:
            email = self.email

        is_pod_member = self.is_pod_member

        is_you = self.is_you

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "user_id": user_id,
            }
        )
        if email is not UNSET:
            field_dict["email"] = email
        if is_pod_member is not UNSET:
            field_dict["is_pod_member"] = is_pod_member
        if is_you is not UNSET:
            field_dict["is_you"] = is_you
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        user_id = UUID(d.pop("user_id"))

        def _parse_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email = _parse_email(d.pop("email", UNSET))

        is_pod_member = d.pop("is_pod_member", UNSET)

        is_you = d.pop("is_you", UNSET)

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        surface_connection_owner = cls(
            user_id=user_id,
            email=email,
            is_pod_member=is_pod_member,
            is_you=is_you,
            name=name,
        )

        surface_connection_owner.additional_properties = d
        return surface_connection_owner

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
