from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.surface_platform import SurfacePlatform
from ..types import UNSET, Unset

T = TypeVar("T", bound="SetDefaultSurfaceRequest")


@_attrs_define
class SetDefaultSurfaceRequest:
    """Pick which surface answers this user for ``platform`` when several could.

    Exactly one of ``surface_id`` (a surface that already exists) or ``pod_id``
    (a pod to be answered from on the platform's shared bot, whose surface is
    made if it has none yet).

        Attributes:
            platform (SurfacePlatform): The platforms a pod can be reached on.

                Email is Resend, and only Resend. Gmail and Outlook were here as
                Composio-backed mailboxes, which made "an email surface" mean three
                different transports with three attachment strategies between them -- bytes,
                Graph drafts, and a signed URL the provider downloads server-side. Reaching
                a Gmail *account* is still something an agent does, through the connector;
                it is just not a surface.
            pod_id (None | Unset | UUID):
            surface_id (None | Unset | UUID):
    """

    platform: SurfacePlatform
    pod_id: None | Unset | UUID = UNSET
    surface_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        platform = self.platform.value

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        surface_id: None | str | Unset
        if isinstance(self.surface_id, Unset):
            surface_id = UNSET
        elif isinstance(self.surface_id, UUID):
            surface_id = str(self.surface_id)
        else:
            surface_id = self.surface_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "platform": platform,
            }
        )
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id
        if surface_id is not UNSET:
            field_dict["surface_id"] = surface_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        platform = SurfacePlatform(d.pop("platform"))

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

        def _parse_surface_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                surface_id_type_0 = UUID(data)

                return surface_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        surface_id = _parse_surface_id(d.pop("surface_id", UNSET))

        set_default_surface_request = cls(
            platform=platform,
            pod_id=pod_id,
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
