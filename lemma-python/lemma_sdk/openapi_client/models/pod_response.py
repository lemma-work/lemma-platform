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
    from ..models.pod_config import PodConfig


T = TypeVar("T", bound="PodResponse")


@_attrs_define
class PodResponse:
    """Pod response schema.

    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        name (str):
        organization_id (UUID):
        updated_at (datetime.datetime):
        user_id (UUID):
        config (PodConfig | Unset): Typed pod-level configuration.
        description (None | str | Unset):
        icon_url (None | str | Unset):
    """

    created_at: datetime.datetime
    id: UUID
    name: str
    organization_id: UUID
    updated_at: datetime.datetime
    user_id: UUID
    config: PodConfig | Unset = UNSET
    description: None | str | Unset = UNSET
    icon_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        name = self.name

        organization_id = str(self.organization_id)

        updated_at = self.updated_at.isoformat()

        user_id = str(self.user_id)

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        icon_url: None | str | Unset
        if isinstance(self.icon_url, Unset):
            icon_url = UNSET
        else:
            icon_url = self.icon_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "updated_at": updated_at,
                "user_id": user_id,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config
        if description is not UNSET:
            field_dict["description"] = description
        if icon_url is not UNSET:
            field_dict["icon_url"] = icon_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pod_config import PodConfig

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        name = d.pop("name")

        organization_id = UUID(d.pop("organization_id"))

        updated_at = isoparse(d.pop("updated_at"))

        user_id = UUID(d.pop("user_id"))

        _config = d.pop("config", UNSET)
        config: PodConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = PodConfig.from_dict(_config)

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_icon_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon_url = _parse_icon_url(d.pop("icon_url", UNSET))

        pod_response = cls(
            created_at=created_at,
            id=id,
            name=name,
            organization_id=organization_id,
            updated_at=updated_at,
            user_id=user_id,
            config=config,
            description=description,
            icon_url=icon_url,
        )

        pod_response.additional_properties = d
        return pod_response

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
