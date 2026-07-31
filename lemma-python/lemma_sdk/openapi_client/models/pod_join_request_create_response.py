from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.organization_role import OrganizationRole
from ..models.pod_join_request_status import PodJoinRequestStatus
from ..models.pod_role import PodRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="PodJoinRequestCreateResponse")


@_attrs_define
class PodJoinRequestCreateResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        organization_id (UUID):
        pod_id (UUID):
        requested_at (datetime.datetime):
        status (PodJoinRequestStatus):
        updated_at (datetime.datetime):
        user_id (UUID):
        approved_at (datetime.datetime | None | Unset):
        approved_by_user_id (None | Unset | UUID):
        org_role (None | OrganizationRole | Unset):
        pod_role (None | PodRole | Unset):
        user_email (None | str | Unset):
        user_name (None | str | Unset):
    """

    created_at: datetime.datetime
    id: UUID
    organization_id: UUID
    pod_id: UUID
    requested_at: datetime.datetime
    status: PodJoinRequestStatus
    updated_at: datetime.datetime
    user_id: UUID
    approved_at: datetime.datetime | None | Unset = UNSET
    approved_by_user_id: None | Unset | UUID = UNSET
    org_role: None | OrganizationRole | Unset = UNSET
    pod_role: None | PodRole | Unset = UNSET
    user_email: None | str | Unset = UNSET
    user_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        organization_id = str(self.organization_id)

        pod_id = str(self.pod_id)

        requested_at = self.requested_at.isoformat()

        status = self.status.value

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        approved_at: None | str | Unset
        if isinstance(self.approved_at, Unset):
            approved_at = UNSET
        elif isinstance(self.approved_at, datetime.datetime):
            approved_at = self.approved_at.isoformat()
        else:
            approved_at = self.approved_at

        approved_by_user_id: None | str | Unset
        if isinstance(self.approved_by_user_id, Unset):
            approved_by_user_id = UNSET
        elif isinstance(self.approved_by_user_id, UUID):
            approved_by_user_id = str(self.approved_by_user_id)
        else:
            approved_by_user_id = self.approved_by_user_id

        org_role: None | str | Unset
        if isinstance(self.org_role, Unset):
            org_role = UNSET
        elif isinstance(self.org_role, OrganizationRole):
            org_role = self.org_role.value
        else:
            org_role = self.org_role

        pod_role: None | str | Unset
        if isinstance(self.pod_role, Unset):
            pod_role = UNSET
        elif isinstance(self.pod_role, PodRole):
            pod_role = self.pod_role.value
        else:
            pod_role = self.pod_role

        user_email: None | str | Unset
        if isinstance(self.user_email, Unset):
            user_email = UNSET
        else:
            user_email = self.user_email

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
                "id": id,
                "organization_id": organization_id,
                "pod_id": pod_id,
                "requested_at": requested_at,
                "status": status,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if approved_at is not UNSET:
            field_dict["approved_at"] = approved_at
        if approved_by_user_id is not UNSET:
            field_dict["approved_by_user_id"] = approved_by_user_id
        if org_role is not UNSET:
            field_dict["org_role"] = org_role
        if pod_role is not UNSET:
            field_dict["pod_role"] = pod_role
        if user_email is not UNSET:
            field_dict["user_email"] = user_email
        if user_name is not UNSET:
            field_dict["user_name"] = user_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        organization_id = UUID(d.pop("organization_id"))

        pod_id = UUID(d.pop("pod_id"))

        requested_at = isoparse(d.pop("requested_at"))

        status = PodJoinRequestStatus(d.pop("status"))

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        def _parse_approved_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                approved_at_type_0 = isoparse(data)

                return approved_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        approved_at = _parse_approved_at(d.pop("approved_at", UNSET))

        def _parse_approved_by_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                approved_by_user_id_type_0 = UUID(data)

                return approved_by_user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        approved_by_user_id = _parse_approved_by_user_id(
            d.pop("approved_by_user_id", UNSET)
        )

        def _parse_org_role(data: object) -> None | OrganizationRole | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                org_role_type_0 = OrganizationRole(data)

                return org_role_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | OrganizationRole | Unset, data)

        org_role = _parse_org_role(d.pop("org_role", UNSET))

        def _parse_pod_role(data: object) -> None | PodRole | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_role_type_0 = PodRole(data)

                return pod_role_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PodRole | Unset, data)

        pod_role = _parse_pod_role(d.pop("pod_role", UNSET))

        def _parse_user_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        user_email = _parse_user_email(d.pop("user_email", UNSET))

        def _parse_user_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        user_name = _parse_user_name(d.pop("user_name", UNSET))

        pod_join_request_create_response = cls(
            created_at=created_at,
            id=id,
            organization_id=organization_id,
            pod_id=pod_id,
            requested_at=requested_at,
            status=status,
            updated_at=updated_at,
            user_id=user_id,
            approved_at=approved_at,
            approved_by_user_id=approved_by_user_id,
            org_role=org_role,
            pod_role=pod_role,
            user_email=user_email,
            user_name=user_name,
        )

        pod_join_request_create_response.additional_properties = d
        return pod_join_request_create_response

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
