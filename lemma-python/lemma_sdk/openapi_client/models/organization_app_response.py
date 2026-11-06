from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="OrganizationAppResponse")


@_attrs_define
class OrganizationAppResponse:
    """
    Attributes:
        id (UUID):
        name (str):
        pod_id (UUID):
        pod_name (str):
        status (str):
        updated_at (datetime.datetime):
        visibility (str):
        description (None | str | Unset):
        url (None | str | Unset):
    """

    id: UUID
    name: str
    pod_id: UUID
    pod_name: str
    status: str
    updated_at: datetime.datetime
    visibility: str
    description: None | str | Unset = UNSET
    url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        name = self.name

        pod_id = str(self.pod_id)

        pod_name = self.pod_name

        status = self.status

        updated_at = self.updated_at.isoformat()

        visibility = self.visibility

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        url: None | str | Unset
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "name": name,
                "pod_id": pod_id,
                "pod_name": pod_name,
                "status": status,
                "updated_at": updated_at,
                "visibility": visibility,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        name = d.pop("name")

        pod_id = UUID(d.pop("pod_id"))

        pod_name = d.pop("pod_name")

        status = d.pop("status")

        updated_at = isoparse(d.pop("updated_at"))

        visibility = d.pop("visibility")

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        url = _parse_url(d.pop("url", UNSET))

        organization_app_response = cls(
            id=id,
            name=name,
            pod_id=pod_id,
            pod_name=pod_name,
            status=status,
            updated_at=updated_at,
            visibility=visibility,
            description=description,
            url=url,
        )

        organization_app_response.additional_properties = d
        return organization_app_response

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
