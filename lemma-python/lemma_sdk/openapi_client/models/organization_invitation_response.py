from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.organization_invitation_status import OrganizationInvitationStatus
from ..models.organization_role import OrganizationRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="OrganizationInvitationResponse")


@_attrs_define
class OrganizationInvitationResponse:
    """Organization invitation response schema.

    Attributes:
        created_at (datetime.datetime):
        email (str):
        expires_at (datetime.datetime):
        id (UUID):
        organization_id (UUID):
        role (OrganizationRole): Roles for organization membership.
        status (OrganizationInvitationStatus): Statuses for organization invitations.
        updated_at (datetime.datetime):
        accepted_at (datetime.datetime | None | Unset):
        organization_name (None | str | Unset):
        pod_description (None | str | Unset):
        pod_id (None | Unset | UUID):
        pod_name (None | str | Unset):
        pod_role (None | str | Unset):
        redirect_uri (None | str | Unset):
        revoked_at (datetime.datetime | None | Unset):
    """

    created_at: datetime.datetime
    email: str
    expires_at: datetime.datetime
    id: UUID
    organization_id: UUID
    role: OrganizationRole
    status: OrganizationInvitationStatus
    updated_at: datetime.datetime
    accepted_at: datetime.datetime | None | Unset = UNSET
    organization_name: None | str | Unset = UNSET
    pod_description: None | str | Unset = UNSET
    pod_id: None | Unset | UUID = UNSET
    pod_name: None | str | Unset = UNSET
    pod_role: None | str | Unset = UNSET
    redirect_uri: None | str | Unset = UNSET
    revoked_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        email = self.email

        expires_at = self.expires_at.isoformat()

        id = str(self.id)

        organization_id = str(self.organization_id)

        role = self.role.value

        status = self.status.value

        updated_at = self.updated_at.isoformat()

        accepted_at: None | str | Unset
        if isinstance(self.accepted_at, Unset):
            accepted_at = UNSET
        elif isinstance(self.accepted_at, datetime.datetime):
            accepted_at = self.accepted_at.isoformat()
        else:
            accepted_at = self.accepted_at

        organization_name: None | str | Unset
        if isinstance(self.organization_name, Unset):
            organization_name = UNSET
        else:
            organization_name = self.organization_name

        pod_description: None | str | Unset
        if isinstance(self.pod_description, Unset):
            pod_description = UNSET
        else:
            pod_description = self.pod_description

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        pod_name: None | str | Unset
        if isinstance(self.pod_name, Unset):
            pod_name = UNSET
        else:
            pod_name = self.pod_name

        pod_role: None | str | Unset
        if isinstance(self.pod_role, Unset):
            pod_role = UNSET
        else:
            pod_role = self.pod_role

        redirect_uri: None | str | Unset
        if isinstance(self.redirect_uri, Unset):
            redirect_uri = UNSET
        else:
            redirect_uri = self.redirect_uri

        revoked_at: None | str | Unset
        if isinstance(self.revoked_at, Unset):
            revoked_at = UNSET
        elif isinstance(self.revoked_at, datetime.datetime):
            revoked_at = self.revoked_at.isoformat()
        else:
            revoked_at = self.revoked_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "email": email,
                "expires_at": expires_at,
                "id": id,
                "organization_id": organization_id,
                "role": role,
                "status": status,
                "updated_at": updated_at,
            }
        )
        if accepted_at is not UNSET:
            field_dict["accepted_at"] = accepted_at
        if organization_name is not UNSET:
            field_dict["organization_name"] = organization_name
        if pod_description is not UNSET:
            field_dict["pod_description"] = pod_description
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id
        if pod_name is not UNSET:
            field_dict["pod_name"] = pod_name
        if pod_role is not UNSET:
            field_dict["pod_role"] = pod_role
        if redirect_uri is not UNSET:
            field_dict["redirect_uri"] = redirect_uri
        if revoked_at is not UNSET:
            field_dict["revoked_at"] = revoked_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        email = d.pop("email")

        expires_at = isoparse(d.pop("expires_at"))

        id = UUID(d.pop("id"))

        organization_id = UUID(d.pop("organization_id"))

        role = OrganizationRole(d.pop("role"))

        status = OrganizationInvitationStatus(d.pop("status"))

        updated_at = isoparse(d.pop("updated_at"))

        def _parse_accepted_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                accepted_at_type_0 = isoparse(data)

                return accepted_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        accepted_at = _parse_accepted_at(d.pop("accepted_at", UNSET))

        def _parse_organization_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        organization_name = _parse_organization_name(d.pop("organization_name", UNSET))

        def _parse_pod_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pod_description = _parse_pod_description(d.pop("pod_description", UNSET))

        def _parse_pod_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_id_type_0 = UUID(data)

                return pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        pod_id = _parse_pod_id(d.pop("pod_id", UNSET))

        def _parse_pod_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pod_name = _parse_pod_name(d.pop("pod_name", UNSET))

        def _parse_pod_role(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pod_role = _parse_pod_role(d.pop("pod_role", UNSET))

        def _parse_redirect_uri(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        redirect_uri = _parse_redirect_uri(d.pop("redirect_uri", UNSET))

        def _parse_revoked_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                revoked_at_type_0 = isoparse(data)

                return revoked_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        revoked_at = _parse_revoked_at(d.pop("revoked_at", UNSET))

        organization_invitation_response = cls(
            created_at=created_at,
            email=email,
            expires_at=expires_at,
            id=id,
            organization_id=organization_id,
            role=role,
            status=status,
            updated_at=updated_at,
            accepted_at=accepted_at,
            organization_name=organization_name,
            pod_description=pod_description,
            pod_id=pod_id,
            pod_name=pod_name,
            pod_role=pod_role,
            redirect_uri=redirect_uri,
            revoked_at=revoked_at,
        )

        organization_invitation_response.additional_properties = d
        return organization_invitation_response

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
