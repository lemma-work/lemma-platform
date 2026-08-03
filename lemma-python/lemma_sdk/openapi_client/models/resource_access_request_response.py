from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="ResourceAccessRequestResponse")


@_attrs_define
class ResourceAccessRequestResponse:
    """
    Attributes:
        id (UUID):
        pod_id (UUID):
        requested_at (datetime.datetime):
        requester_user_id (UUID):
        resource_id (UUID):
        resource_type (str):
        status (str):
        decided_at (datetime.datetime | None | Unset):
        decided_by_user_id (None | Unset | UUID):
        message (None | str | Unset):
        requested_permission_ids (list[str] | Unset):
        requester_email (None | str | Unset):
        requester_name (None | str | Unset):
        resource_name (None | str | Unset):
    """

    id: UUID
    pod_id: UUID
    requested_at: datetime.datetime
    requester_user_id: UUID
    resource_id: UUID
    resource_type: str
    status: str
    decided_at: datetime.datetime | None | Unset = UNSET
    decided_by_user_id: None | Unset | UUID = UNSET
    message: None | str | Unset = UNSET
    requested_permission_ids: list[str] | Unset = UNSET
    requester_email: None | str | Unset = UNSET
    requester_name: None | str | Unset = UNSET
    resource_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        pod_id = str(self.pod_id)

        requested_at = self.requested_at.isoformat()

        requester_user_id = str(self.requester_user_id)

        resource_id = str(self.resource_id)

        resource_type = self.resource_type

        status = self.status

        decided_at: None | str | Unset
        if isinstance(self.decided_at, Unset):
            decided_at = UNSET
        elif isinstance(self.decided_at, datetime.datetime):
            decided_at = self.decided_at.isoformat()
        else:
            decided_at = self.decided_at

        decided_by_user_id: None | str | Unset
        if isinstance(self.decided_by_user_id, Unset):
            decided_by_user_id = UNSET
        elif isinstance(self.decided_by_user_id, UUID):
            decided_by_user_id = str(self.decided_by_user_id)
        else:
            decided_by_user_id = self.decided_by_user_id

        message: None | str | Unset
        if isinstance(self.message, Unset):
            message = UNSET
        else:
            message = self.message

        requested_permission_ids: list[str] | Unset = UNSET
        if not isinstance(self.requested_permission_ids, Unset):
            requested_permission_ids = self.requested_permission_ids

        requester_email: None | str | Unset
        if isinstance(self.requester_email, Unset):
            requester_email = UNSET
        else:
            requester_email = self.requester_email

        requester_name: None | str | Unset
        if isinstance(self.requester_name, Unset):
            requester_name = UNSET
        else:
            requester_name = self.requester_name

        resource_name: None | str | Unset
        if isinstance(self.resource_name, Unset):
            resource_name = UNSET
        else:
            resource_name = self.resource_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "pod_id": pod_id,
                "requested_at": requested_at,
                "requester_user_id": requester_user_id,
                "resource_id": resource_id,
                "resource_type": resource_type,
                "status": status,
            }
        )
        if decided_at is not UNSET:
            field_dict["decided_at"] = decided_at
        if decided_by_user_id is not UNSET:
            field_dict["decided_by_user_id"] = decided_by_user_id
        if message is not UNSET:
            field_dict["message"] = message
        if requested_permission_ids is not UNSET:
            field_dict["requested_permission_ids"] = requested_permission_ids
        if requester_email is not UNSET:
            field_dict["requester_email"] = requester_email
        if requester_name is not UNSET:
            field_dict["requester_name"] = requester_name
        if resource_name is not UNSET:
            field_dict["resource_name"] = resource_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        pod_id = UUID(d.pop("pod_id"))

        requested_at = isoparse(d.pop("requested_at"))

        requester_user_id = UUID(d.pop("requester_user_id"))

        resource_id = UUID(d.pop("resource_id"))

        resource_type = d.pop("resource_type")

        status = d.pop("status")

        def _parse_decided_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                decided_at_type_0 = isoparse(data)

                return decided_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        decided_at = _parse_decided_at(d.pop("decided_at", UNSET))

        def _parse_decided_by_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                decided_by_user_id_type_0 = UUID(data)

                return decided_by_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        decided_by_user_id = _parse_decided_by_user_id(
            d.pop("decided_by_user_id", UNSET)
        )

        def _parse_message(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        message = _parse_message(d.pop("message", UNSET))

        requested_permission_ids = cast(
            list[str], d.pop("requested_permission_ids", UNSET)
        )

        def _parse_requester_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        requester_email = _parse_requester_email(d.pop("requester_email", UNSET))

        def _parse_requester_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        requester_name = _parse_requester_name(d.pop("requester_name", UNSET))

        def _parse_resource_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        resource_name = _parse_resource_name(d.pop("resource_name", UNSET))

        resource_access_request_response = cls(
            id=id,
            pod_id=pod_id,
            requested_at=requested_at,
            requester_user_id=requester_user_id,
            resource_id=resource_id,
            resource_type=resource_type,
            status=status,
            decided_at=decided_at,
            decided_by_user_id=decided_by_user_id,
            message=message,
            requested_permission_ids=requested_permission_ids,
            requester_email=requester_email,
            requester_name=requester_name,
            resource_name=resource_name,
        )

        resource_access_request_response.additional_properties = d
        return resource_access_request_response

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
