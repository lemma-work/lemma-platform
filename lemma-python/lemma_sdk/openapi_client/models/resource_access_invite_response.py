from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="ResourceAccessInviteResponse")


@_attrs_define
class ResourceAccessInviteResponse:
    """
    Attributes:
        email (str):
        id (UUID):
        invited_at (datetime.datetime):
        pod_id (UUID):
        resource_id (UUID):
        resource_type (str):
        status (str):
        invited_by_user_id (None | Unset | UUID):
        permission_ids (list[str] | Unset):
        redeemed_at (datetime.datetime | None | Unset):
        resource_name (None | str | Unset):
    """

    email: str
    id: UUID
    invited_at: datetime.datetime
    pod_id: UUID
    resource_id: UUID
    resource_type: str
    status: str
    invited_by_user_id: None | Unset | UUID = UNSET
    permission_ids: list[str] | Unset = UNSET
    redeemed_at: datetime.datetime | None | Unset = UNSET
    resource_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        email = self.email

        id = str(self.id)

        invited_at = self.invited_at.isoformat()

        pod_id = str(self.pod_id)

        resource_id = str(self.resource_id)

        resource_type = self.resource_type

        status = self.status

        invited_by_user_id: None | str | Unset
        if isinstance(self.invited_by_user_id, Unset):
            invited_by_user_id = UNSET
        elif isinstance(self.invited_by_user_id, UUID):
            invited_by_user_id = str(self.invited_by_user_id)
        else:
            invited_by_user_id = self.invited_by_user_id

        permission_ids: list[str] | Unset = UNSET
        if not isinstance(self.permission_ids, Unset):
            permission_ids = self.permission_ids

        redeemed_at: None | str | Unset
        if isinstance(self.redeemed_at, Unset):
            redeemed_at = UNSET
        elif isinstance(self.redeemed_at, datetime.datetime):
            redeemed_at = self.redeemed_at.isoformat()
        else:
            redeemed_at = self.redeemed_at

        resource_name: None | str | Unset
        if isinstance(self.resource_name, Unset):
            resource_name = UNSET
        else:
            resource_name = self.resource_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "email": email,
                "id": id,
                "invited_at": invited_at,
                "pod_id": pod_id,
                "resource_id": resource_id,
                "resource_type": resource_type,
                "status": status,
            }
        )
        if invited_by_user_id is not UNSET:
            field_dict["invited_by_user_id"] = invited_by_user_id
        if permission_ids is not UNSET:
            field_dict["permission_ids"] = permission_ids
        if redeemed_at is not UNSET:
            field_dict["redeemed_at"] = redeemed_at
        if resource_name is not UNSET:
            field_dict["resource_name"] = resource_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        email = d.pop("email")

        id = UUID(d.pop("id"))

        invited_at = isoparse(d.pop("invited_at"))

        pod_id = UUID(d.pop("pod_id"))

        resource_id = UUID(d.pop("resource_id"))

        resource_type = d.pop("resource_type")

        status = d.pop("status")

        def _parse_invited_by_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                invited_by_user_id_type_0 = UUID(data)

                return invited_by_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        invited_by_user_id = _parse_invited_by_user_id(
            d.pop("invited_by_user_id", UNSET)
        )

        permission_ids = cast(list[str], d.pop("permission_ids", UNSET))

        def _parse_redeemed_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                redeemed_at_type_0 = isoparse(data)

                return redeemed_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        redeemed_at = _parse_redeemed_at(d.pop("redeemed_at", UNSET))

        def _parse_resource_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        resource_name = _parse_resource_name(d.pop("resource_name", UNSET))

        resource_access_invite_response = cls(
            email=email,
            id=id,
            invited_at=invited_at,
            pod_id=pod_id,
            resource_id=resource_id,
            resource_type=resource_type,
            status=status,
            invited_by_user_id=invited_by_user_id,
            permission_ids=permission_ids,
            redeemed_at=redeemed_at,
            resource_name=resource_name,
        )

        resource_access_invite_response.additional_properties = d
        return resource_access_invite_response

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
