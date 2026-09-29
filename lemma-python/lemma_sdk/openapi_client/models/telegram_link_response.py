from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="TelegramLinkResponse")


@_attrs_define
class TelegramLinkResponse:
    """A one-time link that connects whoever opens it in Telegram to this user.

    Single use and short-lived; open it straight away rather than sharing it.

        Attributes:
            bot_username (str):
            expires_at (datetime.datetime):
            url (str):
            pod_id (None | Unset | UUID):
    """

    bot_username: str
    expires_at: datetime.datetime
    url: str
    pod_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bot_username = self.bot_username

        expires_at = self.expires_at.isoformat()

        url = self.url

        pod_id: None | str | Unset
        if isinstance(self.pod_id, Unset):
            pod_id = UNSET
        elif isinstance(self.pod_id, UUID):
            pod_id = str(self.pod_id)
        else:
            pod_id = self.pod_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bot_username": bot_username,
                "expires_at": expires_at,
                "url": url,
            }
        )
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        bot_username = d.pop("bot_username")

        expires_at = isoparse(d.pop("expires_at"))

        url = d.pop("url")

        def _parse_pod_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                pod_id_type_0 = UUID(data)

                return pod_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        pod_id = _parse_pod_id(d.pop("pod_id", UNSET))

        telegram_link_response = cls(
            bot_username=bot_username,
            expires_at=expires_at,
            url=url,
            pod_id=pod_id,
        )

        telegram_link_response.additional_properties = d
        return telegram_link_response

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
