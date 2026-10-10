from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.follow_up_channel import FollowUpChannel
from ..types import UNSET, Unset

T = TypeVar("T", bound="FollowUpRequest")


@_attrs_define
class FollowUpRequest:
    """
    Attributes:
        message (str):
        channel (FollowUpChannel | Unset): Where a follow-up goes.

            ``latest``: the contact's most recent conversation, on its own channel.
            ``email``: their verified email address -- that conversation when it is
            an email thread, a new thread from the pod's address when it is not.
    """

    message: str
    channel: FollowUpChannel | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        message = self.message

        channel: str | Unset = UNSET
        if not isinstance(self.channel, Unset):
            channel = self.channel.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "message": message,
            }
        )
        if channel is not UNSET:
            field_dict["channel"] = channel

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        message = d.pop("message")

        _channel = d.pop("channel", UNSET)
        channel: FollowUpChannel | Unset
        if isinstance(_channel, Unset):
            channel = UNSET
        else:
            channel = FollowUpChannel(_channel)

        follow_up_request = cls(
            message=message,
            channel=channel,
        )

        follow_up_request.additional_properties = d
        return follow_up_request

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
