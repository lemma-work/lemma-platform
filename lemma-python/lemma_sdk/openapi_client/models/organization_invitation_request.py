from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.organization_role import OrganizationRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="OrganizationInvitationRequest")


@_attrs_define
class OrganizationInvitationRequest:
    """Organization invitation request schema.

    Attributes:
        email (str):
        role (OrganizationRole): Roles for organization membership.
        pod_id (None | Unset | UUID):
        pod_role (None | str | Unset):
        redirect_uri (None | str | Unset):
    """

    email: str
    role: OrganizationRole
    pod_id: None | Unset | UUID = UNSET
    pod_role: None | str | Unset = UNSET
    redirect_uri: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        email = self.email

        role = self.role.value

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

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

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "email": email,
                "role": role,
            }
        )
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id
        if pod_role is not UNSET:
            field_dict["pod_role"] = pod_role
        if redirect_uri is not UNSET:
            field_dict["redirect_uri"] = redirect_uri

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        email = d.pop("email")

        role = OrganizationRole(d.pop("role"))

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

        organization_invitation_request = cls(
            email=email,
            role=role,
            pod_id=pod_id,
            pod_role=pod_role,
            redirect_uri=redirect_uri,
        )

        organization_invitation_request.additional_properties = d
        return organization_invitation_request

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
