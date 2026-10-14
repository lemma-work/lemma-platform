from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="SlackPersonLinkRequest")


@_attrs_define
class SlackPersonLinkRequest:
    """The values in the link a Slack user's App Home showed them.

    Attributes:
        expires (int):
        signature (str):
        slack_user_id (str):
        team_id (str):
    """

    expires: int
    signature: str
    slack_user_id: str
    team_id: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expires = self.expires

        signature = self.signature

        slack_user_id = self.slack_user_id

        team_id = self.team_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expires": expires,
                "signature": signature,
                "slack_user_id": slack_user_id,
                "team_id": team_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expires = d.pop("expires")

        signature = d.pop("signature")

        slack_user_id = d.pop("slack_user_id")

        team_id = d.pop("team_id")

        slack_person_link_request = cls(
            expires=expires,
            signature=signature,
            slack_user_id=slack_user_id,
            team_id=team_id,
        )

        slack_person_link_request.additional_properties = d
        return slack_person_link_request

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
