from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.surface_slack_config_response_dm_agent_by_user import (
        SurfaceSlackConfigResponseDmAgentByUser,
    )


T = TypeVar("T", bound="SurfaceSlackConfigResponse")


@_attrs_define
class SurfaceSlackConfigResponse:
    """Slack settings as read back. ``dm_agent_by_user`` maps a Slack user id to
    the agent that person chose, or ``__pod_assistant__`` when they explicitly
    chose the pod assistant. A user absent from the map has never chosen and
    falls to the surface default.

        Attributes:
            app_name (None | str | Unset):
            dm_agent_by_user (SurfaceSlackConfigResponseDmAgentByUser | Unset):
    """

    app_name: None | str | Unset = UNSET
    dm_agent_by_user: SurfaceSlackConfigResponseDmAgentByUser | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        app_name: None | str | Unset
        if isinstance(self.app_name, Unset):
            app_name = UNSET
        else:
            app_name = self.app_name

        dm_agent_by_user: dict[str, Any] | Unset = UNSET
        if not isinstance(self.dm_agent_by_user, Unset):
            dm_agent_by_user = self.dm_agent_by_user.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if app_name is not UNSET:
            field_dict["app_name"] = app_name
        if dm_agent_by_user is not UNSET:
            field_dict["dm_agent_by_user"] = dm_agent_by_user

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.surface_slack_config_response_dm_agent_by_user import (
            SurfaceSlackConfigResponseDmAgentByUser,
        )

        d = dict(src_dict)

        def _parse_app_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        app_name = _parse_app_name(d.pop("app_name", UNSET))

        _dm_agent_by_user = d.pop("dm_agent_by_user", UNSET)
        dm_agent_by_user: SurfaceSlackConfigResponseDmAgentByUser | Unset
        if isinstance(_dm_agent_by_user, Unset):
            dm_agent_by_user = UNSET
        else:
            dm_agent_by_user = SurfaceSlackConfigResponseDmAgentByUser.from_dict(
                _dm_agent_by_user
            )

        surface_slack_config_response = cls(
            app_name=app_name,
            dm_agent_by_user=dm_agent_by_user,
        )

        surface_slack_config_response.additional_properties = d
        return surface_slack_config_response

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
