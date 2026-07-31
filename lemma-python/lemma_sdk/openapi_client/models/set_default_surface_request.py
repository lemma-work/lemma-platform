from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.surface_platform import SurfacePlatform

T = TypeVar("T", bound="SetDefaultSurfaceRequest")


@_attrs_define
class SetDefaultSurfaceRequest:
    """Pick which surface answers this user for ``platform`` when several could.

    Attributes:
        platform (SurfacePlatform):
        surface_id (UUID):
    """

    platform: SurfacePlatform
    surface_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        platform = self.platform.value

        surface_id = str(self.surface_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "platform": platform,
                "surface_id": surface_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        platform = SurfacePlatform(d.pop("platform"))

        surface_id = UUID(d.pop("surface_id"))

        set_default_surface_request = cls(
            platform=platform,
            surface_id=surface_id,
        )

        set_default_surface_request.additional_properties = d
        return set_default_surface_request

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
