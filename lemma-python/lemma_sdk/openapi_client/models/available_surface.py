from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.surface_credential_mode import SurfaceCredentialMode
from ..models.surface_platform import SurfacePlatform
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_connect_descriptor import SurfaceConnectDescriptor


T = TypeVar("T", bound="AvailableSurface")


@_attrs_define
class AvailableSurface:
    """One connectable surface platform. ``supported_credential_modes`` is the
    single source of truth for how it can be set up: ``[CUSTOM]`` means an account
    must be connected; ``[CUSTOM, SYSTEM]`` means a Lemma-managed bot can also run
    with no account. The frontend derives ``account_needed = SYSTEM not in modes``
    and ``system_bot_available = SYSTEM in modes``.

        Attributes:
            connector_id (str):
            platform (SurfacePlatform):
            provider (str):
            supported_credential_modes (list[SurfaceCredentialMode]):
            connect (None | SurfaceConnectDescriptor | Unset):
            connector_available (bool | Unset):  Default: True.
            description (None | str | Unset):
            icon (None | str | Unset):
            title (None | str | Unset):
    """

    connector_id: str
    platform: SurfacePlatform
    provider: str
    supported_credential_modes: list[SurfaceCredentialMode]
    connect: None | SurfaceConnectDescriptor | Unset = UNSET
    connector_available: bool | Unset = True
    description: None | str | Unset = UNSET
    icon: None | str | Unset = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.surface_connect_descriptor import SurfaceConnectDescriptor

        connector_id = self.connector_id

        platform = self.platform.value

        provider = self.provider

        supported_credential_modes = []
        for supported_credential_modes_item_data in self.supported_credential_modes:
            supported_credential_modes_item = supported_credential_modes_item_data.value
            supported_credential_modes.append(supported_credential_modes_item)

        connect: dict[str, Any] | None | Unset
        if isinstance(self.connect, Unset):
            connect = UNSET
        elif isinstance(self.connect, SurfaceConnectDescriptor):
            connect = self.connect.to_dict()
        else:
            connect = self.connect

        connector_available = self.connector_available

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        icon: None | str | Unset
        if isinstance(self.icon, Unset):
            icon = UNSET
        else:
            icon = self.icon

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connector_id": connector_id,
                "platform": platform,
                "provider": provider,
                "supported_credential_modes": supported_credential_modes,
            }
        )
        if connect is not UNSET:
            field_dict["connect"] = connect
        if connector_available is not UNSET:
            field_dict["connector_available"] = connector_available
        if description is not UNSET:
            field_dict["description"] = description
        if icon is not UNSET:
            field_dict["icon"] = icon
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_connect_descriptor import SurfaceConnectDescriptor

        d = dict(src_dict)
        connector_id = d.pop("connector_id")

        platform = SurfacePlatform(d.pop("platform"))

        provider = d.pop("provider")

        supported_credential_modes = []
        _supported_credential_modes = d.pop("supported_credential_modes")
        for supported_credential_modes_item_data in _supported_credential_modes:
            supported_credential_modes_item = SurfaceCredentialMode(
                supported_credential_modes_item_data
            )

            supported_credential_modes.append(supported_credential_modes_item)

        def _parse_connect(data: object) -> None | SurfaceConnectDescriptor | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                connect_type_0 = SurfaceConnectDescriptor.from_dict(data)

                return connect_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SurfaceConnectDescriptor | Unset, data)

        connect = _parse_connect(d.pop("connect", UNSET))

        connector_available = d.pop("connector_available", UNSET)

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_icon(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        icon = _parse_icon(d.pop("icon", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        available_surface = cls(
            connector_id=connector_id,
            platform=platform,
            provider=provider,
            supported_credential_modes=supported_credential_modes,
            connect=connect,
            connector_available=connector_available,
            description=description,
            icon=icon,
            title=title,
        )

        available_surface.additional_properties = d
        return available_surface

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
