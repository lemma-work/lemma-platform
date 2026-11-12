from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConsentResponse")


@_attrs_define
class ConsentResponse:
    """
    Attributes:
        app_id (UUID):
        granted_at (datetime.datetime):
        pod_id (UUID):
        app_name (None | str | Unset):
    """

    app_id: UUID
    granted_at: datetime.datetime
    pod_id: UUID
    app_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        app_id = str(self.app_id)

        granted_at = self.granted_at.isoformat()

        pod_id = str(self.pod_id)

        app_name: None | str | Unset
        if isinstance(self.app_name, Unset):
            app_name = UNSET
        else:
            app_name = self.app_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "app_id": app_id,
                "granted_at": granted_at,
                "pod_id": pod_id,
            }
        )
        if app_name is not UNSET:
            field_dict["app_name"] = app_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app_id = UUID(d.pop("app_id"))

        granted_at = isoparse(d.pop("granted_at"))

        pod_id = UUID(d.pop("pod_id"))

        def _parse_app_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        app_name = _parse_app_name(d.pop("app_name", UNSET))

        consent_response = cls(
            app_id=app_id,
            granted_at=granted_at,
            pod_id=pod_id,
            app_name=app_name,
        )

        consent_response.additional_properties = d
        return consent_response

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
