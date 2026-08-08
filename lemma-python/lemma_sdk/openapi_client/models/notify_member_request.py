from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="NotifyMemberRequest")


@_attrs_define
class NotifyMemberRequest:
    """Send a notification to one pod member.

    Attributes:
        body (str):
        recipient (str): Pod member id, user id, or email address of the recipient.
        title (str):
        background_instruction (None | str | Unset): Never shown to the recipient. Tells the agent that handles their
            reply what to do with it.
        expects_response (bool | Unset):  Default: True.
        expires_in_seconds (int | None | Unset):
    """

    body: str
    recipient: str
    title: str
    background_instruction: None | str | Unset = UNSET
    expects_response: bool | Unset = True
    expires_in_seconds: int | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        body = self.body

        recipient = self.recipient

        title = self.title

        background_instruction: None | str | Unset
        if isinstance(self.background_instruction, Unset):
            background_instruction = UNSET
        else:
            background_instruction = self.background_instruction

        expects_response = self.expects_response

        expires_in_seconds: int | None | Unset
        if isinstance(self.expires_in_seconds, Unset):
            expires_in_seconds = UNSET
        else:
            expires_in_seconds = self.expires_in_seconds

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "body": body,
                "recipient": recipient,
                "title": title,
            }
        )
        if background_instruction is not UNSET:
            field_dict["background_instruction"] = background_instruction
        if expects_response is not UNSET:
            field_dict["expects_response"] = expects_response
        if expires_in_seconds is not UNSET:
            field_dict["expires_in_seconds"] = expires_in_seconds

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        body = d.pop("body")

        recipient = d.pop("recipient")

        title = d.pop("title")

        def _parse_background_instruction(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        background_instruction = _parse_background_instruction(
            d.pop("background_instruction", UNSET)
        )

        expects_response = d.pop("expects_response", UNSET)

        def _parse_expires_in_seconds(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        expires_in_seconds = _parse_expires_in_seconds(
            d.pop("expires_in_seconds", UNSET)
        )

        notify_member_request = cls(
            body=body,
            recipient=recipient,
            title=title,
            background_instruction=background_instruction,
            expects_response=expects_response,
            expires_in_seconds=expires_in_seconds,
        )

        return notify_member_request
