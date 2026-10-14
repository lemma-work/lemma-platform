from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="SlackAppWorkspaceResponse")


@_attrs_define
class SlackAppWorkspaceResponse:
    """A Slack workspace Lemma can create apps in. Carries no token.

    Attributes:
        saved_at (datetime.datetime):
        team_id (str):
        team_name (None | str | Unset):
    """

    saved_at: datetime.datetime
    team_id: str
    team_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        saved_at = self.saved_at.isoformat()

        team_id = self.team_id

        team_name: None | str | Unset
        if isinstance(self.team_name, Unset):
            team_name = UNSET
        else:
            team_name = self.team_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "saved_at": saved_at,
                "team_id": team_id,
            }
        )
        if team_name is not UNSET:
            field_dict["team_name"] = team_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        saved_at = isoparse(d.pop("saved_at"))

        team_id = d.pop("team_id")

        def _parse_team_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        team_name = _parse_team_name(d.pop("team_name", UNSET))

        slack_app_workspace_response = cls(
            saved_at=saved_at,
            team_id=team_id,
            team_name=team_name,
        )

        slack_app_workspace_response.additional_properties = d
        return slack_app_workspace_response

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
