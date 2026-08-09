from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceChannelRouteInput")


@_attrs_define
class SurfaceChannelRouteInput:
    """One channel's routing, in the same three states the domain models.

    ``use_pod_assistant`` is not a synonym for an absent ``agent_name`` — see
    :class:`SurfaceChannelRoute`. Omitting it here is what silently turned an
    explicit "the pod assistant answers here", picked from inside Slack, back
    into "unconfigured" on the next save from the web UI.

        Attributes:
            agent_name (None | str | Unset):
            channel_id (None | str | Unset):
            channel_name (None | str | Unset):
            use_pod_assistant (bool | Unset):  Default: False.
    """

    agent_name: None | str | Unset = UNSET
    channel_id: None | str | Unset = UNSET
    channel_name: None | str | Unset = UNSET
    use_pod_assistant: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        agent_name: None | str | Unset
        if isinstance(self.agent_name, Unset):
            agent_name = UNSET
        else:
            agent_name = self.agent_name

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

        use_pod_assistant = self.use_pod_assistant

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if agent_name is not UNSET:
            field_dict["agent_name"] = agent_name
        if channel_id is not UNSET:
            field_dict["channel_id"] = channel_id
        if channel_name is not UNSET:
            field_dict["channel_name"] = channel_name
        if use_pod_assistant is not UNSET:
            field_dict["use_pod_assistant"] = use_pod_assistant

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_agent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent_name = _parse_agent_name(d.pop("agent_name", UNSET))

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

        use_pod_assistant = d.pop("use_pod_assistant", UNSET)

        surface_channel_route_input = cls(
            agent_name=agent_name,
            channel_id=channel_id,
            channel_name=channel_name,
            use_pod_assistant=use_pod_assistant,
        )

        return surface_channel_route_input
