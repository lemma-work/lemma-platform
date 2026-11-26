from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.github_bot import GithubBot
    from ..models.pod_connection_item import PodConnectionItem


T = TypeVar("T", bound="PodConnectionsResponse")


@_attrs_define
class PodConnectionsResponse:
    """
    Attributes:
        items (list[PodConnectionItem]):
        may_manage (bool): The caller holds pod.connector.manage.
        github_bot (GithubBot | None | Unset): Null when the organization has no GitHub App installation an APP
            connector can use: proven, and on a GitHub organization.
    """

    items: list[PodConnectionItem]
    may_manage: bool
    github_bot: GithubBot | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.github_bot import GithubBot

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        may_manage = self.may_manage

        github_bot: dict[str, Any] | None | Unset
        if isinstance(self.github_bot, Unset):
            github_bot = UNSET
        elif isinstance(self.github_bot, GithubBot):
            github_bot = self.github_bot.to_dict()
        else:
            github_bot = self.github_bot

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "items": items,
                "may_manage": may_manage,
            }
        )
        if github_bot is not UNSET:
            field_dict["github_bot"] = github_bot

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.github_bot import GithubBot
        from ..models.pod_connection_item import PodConnectionItem

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = PodConnectionItem.from_dict(items_item_data)

            items.append(items_item)

        may_manage = d.pop("may_manage")

        def _parse_github_bot(data: object) -> GithubBot | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                github_bot_type_0 = GithubBot.from_dict(data)

                return github_bot_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(GithubBot | None | Unset, data)

        github_bot = _parse_github_bot(d.pop("github_bot", UNSET))

        pod_connections_response = cls(
            items=items,
            may_manage=may_manage,
            github_bot=github_bot,
        )

        pod_connections_response.additional_properties = d
        return pod_connections_response

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
