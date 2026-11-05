from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.source_kind import SourceKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="ChannelSourceResponse")


@_attrs_define
class ChannelSourceResponse:
    """
    Attributes:
        id (UUID):
        is_private (bool): The platform keeps it to its members.
        kind (SourceKind):
        platform (str): Where it is: TELEGRAM, WHATSAPP, SLACK, TEAMS, RESEND, WEB...
        last_inbound_at (datetime.datetime | None | Unset):
        pending (bool | Unset): A group the bot asked the platform for, not confirmed yet. Default: False.
        surface_id (None | Unset | UUID):
        title (None | str | Unset):
    """

    id: UUID
    is_private: bool
    kind: SourceKind
    platform: str
    last_inbound_at: datetime.datetime | None | Unset = UNSET
    pending: bool | Unset = False
    surface_id: None | Unset | UUID = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        is_private = self.is_private

        kind = self.kind.value

        platform = self.platform

        last_inbound_at: None | str | Unset
        if isinstance(self.last_inbound_at, Unset):
            last_inbound_at = UNSET
        elif isinstance(self.last_inbound_at, datetime.datetime):
            last_inbound_at = self.last_inbound_at.isoformat()
        else:
            last_inbound_at = self.last_inbound_at

        pending = self.pending

        surface_id: None | str | Unset
        if isinstance(self.surface_id, Unset):
            surface_id = UNSET
        elif isinstance(self.surface_id, UUID):
            surface_id = str(self.surface_id)
        else:
            surface_id = self.surface_id

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "is_private": is_private,
                "kind": kind,
                "platform": platform,
            }
        )
        if last_inbound_at is not UNSET:
            field_dict["last_inbound_at"] = last_inbound_at
        if pending is not UNSET:
            field_dict["pending"] = pending
        if surface_id is not UNSET:
            field_dict["surface_id"] = surface_id
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        is_private = d.pop("is_private")

        kind = SourceKind(d.pop("kind"))

        platform = d.pop("platform")

        def _parse_last_inbound_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_inbound_at_type_0 = isoparse(data)

                return last_inbound_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_inbound_at = _parse_last_inbound_at(d.pop("last_inbound_at", UNSET))

        pending = d.pop("pending", UNSET)

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

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        channel_source_response = cls(
            id=id,
            is_private=is_private,
            kind=kind,
            platform=platform,
            last_inbound_at=last_inbound_at,
            pending=pending,
            surface_id=surface_id,
            title=title,
        )

        channel_source_response.additional_properties = d
        return channel_source_response

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
