from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

T = TypeVar("T", bound="WebLoginResponse")


@_attrs_define
class WebLoginResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        last_used_at (datetime.datetime | None):
        origin (str):
        updated_at (datetime.datetime):
        working (bool): Whether the stored session still signs you in. False means it stopped working and the next run
            will ask you again.
    """

    created_at: datetime.datetime
    id: UUID
    last_used_at: datetime.datetime | None
    origin: str
    updated_at: datetime.datetime
    working: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        last_used_at: None | str
        if isinstance(self.last_used_at, datetime.datetime):
            last_used_at = self.last_used_at.isoformat()
        else:
            last_used_at = self.last_used_at

        origin = self.origin

        updated_at = self.updated_at.isoformat()

        working = self.working

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "last_used_at": last_used_at,
                "origin": origin,
                "updated_at": updated_at,
                "working": working,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        id = UUID(d.pop("id"))

        def _parse_last_used_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_used_at_type_0 = isoparse(data)

                return last_used_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None, data)

        last_used_at = _parse_last_used_at(d.pop("last_used_at"))

        origin = d.pop("origin")

        updated_at = isoparse(d.pop("updated_at"))

        working = d.pop("working")

        web_login_response = cls(
            created_at=created_at,
            id=id,
            last_used_at=last_used_at,
            origin=origin,
            updated_at=updated_at,
            working=working,
        )

        web_login_response.additional_properties = d
        return web_login_response

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
