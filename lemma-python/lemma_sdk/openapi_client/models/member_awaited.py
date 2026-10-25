from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.member_connect_standing import MemberConnectStanding
from ..types import UNSET, Unset

T = TypeVar("T", bound="MemberAwaited")


@_attrs_define
class MemberAwaited:
    """A member a required per-member connector is still waiting on.

    Who, and why -- never which account: nobody but its owner is shown that.

        Attributes:
            pod_member_id (UUID):
            standing (MemberConnectStanding):
            user_id (UUID):
            email (None | str | Unset):
            name (None | str | Unset):
    """

    pod_member_id: UUID
    standing: MemberConnectStanding
    user_id: UUID
    email: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        pod_member_id = str(self.pod_member_id)

        standing = self.standing.value

        user_id = str(self.user_id)

        email: None | str | Unset
        if isinstance(self.email, Unset):
            email = UNSET
        else:
            email = self.email

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "pod_member_id": pod_member_id,
                "standing": standing,
                "user_id": user_id,
            }
        )
        if email is not UNSET:
            field_dict["email"] = email
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        pod_member_id = UUID(d.pop("pod_member_id"))

        standing = MemberConnectStanding(d.pop("standing"))

        user_id = UUID(d.pop("user_id"))

        def _parse_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email = _parse_email(d.pop("email", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        member_awaited = cls(
            pod_member_id=pod_member_id,
            standing=standing,
            user_id=user_id,
            email=email,
            name=name,
        )

        member_awaited.additional_properties = d
        return member_awaited

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
