from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.surface_platform import SurfacePlatform
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.user_surface_item import UserSurfaceItem


T = TypeVar("T", bound="UserSurfacePlatformGroup")


@_attrs_define
class UserSurfacePlatformGroup:
    """All of a user's surfaces for one platform. ``conflict`` is true when two
    of them answer at the same address, so the user has to say which pod hears
    them (the ``shares_address`` surfaces are the ones to choose between).

        Attributes:
            platform (SurfacePlatform): The platforms a pod can be reached on.

                Email is Resend, and only Resend. Gmail and Outlook were here as
                Composio-backed mailboxes, which made "an email surface" mean three
                different transports with three attachment strategies between them -- bytes,
                Graph drafts, and a signed URL the provider downloads server-side. Reaching
                a Gmail *account* is still something an agent does, through the connector;
                it is just not a surface.
            surfaces (list[UserSurfaceItem]):
            conflict (bool | Unset):  Default: False.
            default_surface_id (None | Unset | UUID):
    """

    platform: SurfacePlatform
    surfaces: list[UserSurfaceItem]
    conflict: bool | Unset = False
    default_surface_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        platform = self.platform.value

        surfaces = []
        for surfaces_item_data in self.surfaces:
            surfaces_item = surfaces_item_data.to_dict()
            surfaces.append(surfaces_item)

        conflict = self.conflict

        default_surface_id: None | str | Unset
        if isinstance(self.default_surface_id, Unset):
            default_surface_id = UNSET
        elif isinstance(self.default_surface_id, UUID):
            default_surface_id = str(self.default_surface_id)
        else:
            default_surface_id = self.default_surface_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "platform": platform,
                "surfaces": surfaces,
            }
        )
        if conflict is not UNSET:
            field_dict["conflict"] = conflict
        if default_surface_id is not UNSET:
            field_dict["default_surface_id"] = default_surface_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.user_surface_item import UserSurfaceItem

        d = dict(src_dict)
        platform = SurfacePlatform(d.pop("platform"))

        surfaces = []
        _surfaces = d.pop("surfaces")
        for surfaces_item_data in _surfaces:
            surfaces_item = UserSurfaceItem.from_dict(surfaces_item_data)

            surfaces.append(surfaces_item)

        conflict = d.pop("conflict", UNSET)

        def _parse_default_surface_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                default_surface_id_type_0 = UUID(data)

                return default_surface_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        default_surface_id = _parse_default_surface_id(
            d.pop("default_surface_id", UNSET)
        )

        user_surface_platform_group = cls(
            platform=platform,
            surfaces=surfaces,
            conflict=conflict,
            default_surface_id=default_surface_id,
        )

        user_surface_platform_group.additional_properties = d
        return user_surface_platform_group

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
