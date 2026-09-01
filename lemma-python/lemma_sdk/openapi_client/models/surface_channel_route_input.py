from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceChannelRouteInput")


@_attrs_define
class SurfaceChannelRouteInput:
    """One channel this surface's agent may be spoken to in.

    An allow-list entry. A surface belongs to exactly one agent, so a channel
    says *where*, never *who* — it used to name an agent, back when one bot
    could serve several.

        Attributes:
            channel_id (None | str | Unset):
            channel_name (None | str | Unset):
    """

    channel_id: None | str | Unset = UNSET
    channel_name: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        channel_id: None | str | Unset
        if isinstance(self.channel_id, Unset):
            channel_id = UNSET
        else:
            channel_id = self.channel_id

        channel_name: None | str | Unset
        if isinstance(self.channel_name, Unset):
            channel_name = UNSET
        else:
            channel_name = self.channel_name

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if channel_id is not UNSET:
            field_dict["channel_id"] = channel_id
        if channel_name is not UNSET:
            field_dict["channel_name"] = channel_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_channel_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        channel_id = _parse_channel_id(d.pop("channel_id", UNSET))

        def _parse_channel_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        channel_name = _parse_channel_name(d.pop("channel_name", UNSET))

        surface_channel_route_input = cls(
            channel_id=channel_id,
            channel_name=channel_name,
        )

        return surface_channel_route_input
