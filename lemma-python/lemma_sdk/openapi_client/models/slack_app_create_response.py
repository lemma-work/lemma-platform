from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SlackAppCreateResponse")


@_attrs_define
class SlackAppCreateResponse:
    """The app Slack made, and the install to authorise it against.

    Attributes:
        app_id (str):
        auth_config_id (UUID):
        reused (bool | Unset): An app made earlier for the same agent and workspace (or the same Idempotency-Key), not a
            new one. Default: False.
    """

    app_id: str
    auth_config_id: UUID
    reused: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        app_id = self.app_id

        auth_config_id = str(self.auth_config_id)

        reused = self.reused

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "app_id": app_id,
                "auth_config_id": auth_config_id,
            }
        )
        if reused is not UNSET:
            field_dict["reused"] = reused

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app_id = d.pop("app_id")

        auth_config_id = UUID(d.pop("auth_config_id"))

        reused = d.pop("reused", UNSET)

        slack_app_create_response = cls(
            app_id=app_id,
            auth_config_id=auth_config_id,
            reused=reused,
        )

        slack_app_create_response.additional_properties = d
        return slack_app_create_response

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
