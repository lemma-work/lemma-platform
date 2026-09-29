from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.telegram_link_pod import TelegramLinkPod


T = TypeVar("T", bound="TelegramLinkOptionsResponse")


@_attrs_define
class TelegramLinkOptionsResponse:
    """What a link to the shared Telegram bot would connect, before minting one.

    Attributes:
        bot_username (str):
        pods (list[TelegramLinkPod]):
        pod_id (None | Unset | UUID):
    """

    bot_username: str
    pods: list[TelegramLinkPod]
    pod_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bot_username = self.bot_username

        pods = []
        for pods_item_data in self.pods:
            pods_item = pods_item_data.to_dict()
            pods.append(pods_item)

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
                "pods": pods,
            }
        )
        if pod_id is not UNSET:
            field_dict["pod_id"] = pod_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.telegram_link_pod import TelegramLinkPod

        d = dict(src_dict)
        bot_username = d.pop("bot_username")

        pods = []
        _pods = d.pop("pods")
        for pods_item_data in _pods:
            pods_item = TelegramLinkPod.from_dict(pods_item_data)

            pods.append(pods_item)

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

        telegram_link_options_response = cls(
            bot_username=bot_username,
            pods=pods,
            pod_id=pod_id,
        )

        telegram_link_options_response.additional_properties = d
        return telegram_link_options_response

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
