from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.send_audience import SendAudience
from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceSendPolicyConfig")


@_attrs_define
class SurfaceSendPolicyConfig:
    """Proactive-send controls. Mirrored across request and response.

    ``audience`` is the field to set. ``allow_send`` is the original boolean,
    still accepted so existing clients and stored bundles keep working: it maps
    to ``SELF`` / ``NOBODY``. When both are present, ``audience`` wins.

        Attributes:
            allow_send (bool | None | Unset):
            audience (SendAudience | Unset): Who an agent on this surface is allowed to reach unprompted.
            max_messages_per_recipient_per_hour (int | Unset):  Default: 6.
    """

    allow_send: bool | None | Unset = UNSET
    audience: SendAudience | Unset = UNSET
    max_messages_per_recipient_per_hour: int | Unset = 6
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        allow_send: bool | None | Unset
        if isinstance(self.allow_send, Unset):
            allow_send = UNSET
        else:
            allow_send = self.allow_send

        audience: str | Unset = UNSET
        if not isinstance(self.audience, Unset):
            audience = self.audience.value

        max_messages_per_recipient_per_hour = self.max_messages_per_recipient_per_hour

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if allow_send is not UNSET:
            field_dict["allow_send"] = allow_send
        if audience is not UNSET:
            field_dict["audience"] = audience
        if max_messages_per_recipient_per_hour is not UNSET:
            field_dict["max_messages_per_recipient_per_hour"] = (
                max_messages_per_recipient_per_hour
            )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_allow_send(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        allow_send = _parse_allow_send(d.pop("allow_send", UNSET))

        _audience = d.pop("audience", UNSET)
        audience: SendAudience | Unset
        if isinstance(_audience, Unset):
            audience = UNSET
        else:
            audience = SendAudience(_audience)

        max_messages_per_recipient_per_hour = d.pop(
            "max_messages_per_recipient_per_hour", UNSET
        )

        surface_send_policy_config = cls(
            allow_send=allow_send,
            audience=audience,
            max_messages_per_recipient_per_hour=max_messages_per_recipient_per_hour,
        )

        surface_send_policy_config.additional_properties = d
        return surface_send_policy_config

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
