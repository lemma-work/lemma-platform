from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SlackPersonLinkPreviewResponse")


@_attrs_define
class SlackPersonLinkPreviewResponse:
    """Whose Slack account a link would connect, before it is connected.

    Attributes:
        team_id (str):
        slack_user_name (None | str | Unset):
        team_name (None | str | Unset):
    """

    team_id: str
    slack_user_name: None | str | Unset = UNSET
    team_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        team_id = self.team_id

        slack_user_name: None | str | Unset
        if isinstance(self.slack_user_name, Unset):
            slack_user_name = UNSET
        else:
            slack_user_name = self.slack_user_name

        team_name: None | str | Unset
        if isinstance(self.team_name, Unset):
            team_name = UNSET
        else:
            team_name = self.team_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "team_id": team_id,
            }
        )
        if slack_user_name is not UNSET:
            field_dict["slack_user_name"] = slack_user_name
        if team_name is not UNSET:
            field_dict["team_name"] = team_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        team_id = d.pop("team_id")

        def _parse_slack_user_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        slack_user_name = _parse_slack_user_name(d.pop("slack_user_name", UNSET))

        def _parse_team_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        team_name = _parse_team_name(d.pop("team_name", UNSET))

        slack_person_link_preview_response = cls(
            team_id=team_id,
            slack_user_name=slack_user_name,
            team_name=team_name,
        )

        slack_person_link_preview_response.additional_properties = d
        return slack_person_link_preview_response

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
