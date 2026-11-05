from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.member_awaited import MemberAwaited


T = TypeVar("T", bound="AskMembersResponse")


@_attrs_define
class AskMembersResponse:
    """Who was asked to connect, and who was asked too recently to ask again.

    Attributes:
        asked (list[MemberAwaited]):
        connect_url (str):
        asked_recently (list[MemberAwaited] | Unset): Already asked about this connector in the last day; not asked
            again.
        not_asked (list[MemberAwaited] | Unset): Not asked, because whether they were asked today could not be checked,
            or the line to them could not be posted, just now. Try again shortly.
    """

    asked: list[MemberAwaited]
    connect_url: str
    asked_recently: list[MemberAwaited] | Unset = UNSET
    not_asked: list[MemberAwaited] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        asked = []
        for asked_item_data in self.asked:
            asked_item = asked_item_data.to_dict()
            asked.append(asked_item)

        connect_url = self.connect_url

        asked_recently: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.asked_recently, Unset):
            asked_recently = []
            for asked_recently_item_data in self.asked_recently:
                asked_recently_item = asked_recently_item_data.to_dict()
                asked_recently.append(asked_recently_item)

        not_asked: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.not_asked, Unset):
            not_asked = []
            for not_asked_item_data in self.not_asked:
                not_asked_item = not_asked_item_data.to_dict()
                not_asked.append(not_asked_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "asked": asked,
                "connect_url": connect_url,
            }
        )
        if asked_recently is not UNSET:
            field_dict["asked_recently"] = asked_recently
        if not_asked is not UNSET:
            field_dict["not_asked"] = not_asked

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.member_awaited import MemberAwaited

        d = dict(src_dict)
        asked = []
        _asked = d.pop("asked")
        for asked_item_data in _asked:
            asked_item = MemberAwaited.from_dict(asked_item_data)

            asked.append(asked_item)

        connect_url = d.pop("connect_url")

        _asked_recently = d.pop("asked_recently", UNSET)
        asked_recently: list[MemberAwaited] | Unset = UNSET
        if _asked_recently is not UNSET:
            asked_recently = []
            for asked_recently_item_data in _asked_recently:
                asked_recently_item = MemberAwaited.from_dict(asked_recently_item_data)

                asked_recently.append(asked_recently_item)

        _not_asked = d.pop("not_asked", UNSET)
        not_asked: list[MemberAwaited] | Unset = UNSET
        if _not_asked is not UNSET:
            not_asked = []
            for not_asked_item_data in _not_asked:
                not_asked_item = MemberAwaited.from_dict(not_asked_item_data)

                not_asked.append(not_asked_item)

        ask_members_response = cls(
            asked=asked,
            connect_url=connect_url,
            asked_recently=asked_recently,
            not_asked=not_asked,
        )

        ask_members_response.additional_properties = d
        return ask_members_response

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
