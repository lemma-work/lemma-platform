from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

T = TypeVar("T", bound="GroupLineResponse")


@_attrs_define
class GroupLineResponse:
    """
    Attributes:
        at (datetime.datetime):
        from_bot (bool):
        in_pod (bool):
        answered_from_public (bool | Unset): On the bot's lines: answered from what the pod made Public. Default: False.
        answered_name (None | str | Unset): On the bot's lines: whom it answered.
        author_external_id (None | str | Unset):
        author_name (None | str | Unset):
        text (None | str | Unset): None where the line is withheld from the reader.
        withheld (bool | Unset): An answer the bot made with another member's own access: shown to that member alone.
            ``answered_name`` still says whom it was for. Default: False.
    """

    at: datetime.datetime
    from_bot: bool
    in_pod: bool
    answered_from_public: bool | Unset = False
    answered_name: None | str | Unset = UNSET
    author_external_id: None | str | Unset = UNSET
    author_name: None | str | Unset = UNSET
    text: None | str | Unset = UNSET
    withheld: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        at = self.at.isoformat()

        from_bot = self.from_bot

        in_pod = self.in_pod

        answered_from_public = self.answered_from_public

        answered_name: None | str | Unset
        if isinstance(self.answered_name, Unset):
            answered_name = UNSET
        else:
            answered_name = self.answered_name

        author_external_id: None | str | Unset
        if isinstance(self.author_external_id, Unset):
            author_external_id = UNSET
        else:
            author_external_id = self.author_external_id

        author_name: None | str | Unset
        if isinstance(self.author_name, Unset):
            author_name = UNSET
        else:
            author_name = self.author_name

        text: None | str | Unset
        if isinstance(self.text, Unset):
            text = UNSET
        else:
            text = self.text

        withheld = self.withheld

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "at": at,
                "from_bot": from_bot,
                "in_pod": in_pod,
            }
        )
        if answered_from_public is not UNSET:
            field_dict["answered_from_public"] = answered_from_public
        if answered_name is not UNSET:
            field_dict["answered_name"] = answered_name
        if author_external_id is not UNSET:
            field_dict["author_external_id"] = author_external_id
        if author_name is not UNSET:
            field_dict["author_name"] = author_name
        if text is not UNSET:
            field_dict["text"] = text
        if withheld is not UNSET:
            field_dict["withheld"] = withheld

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        at = isoparse(d.pop("at"))

        from_bot = d.pop("from_bot")

        in_pod = d.pop("in_pod")

        answered_from_public = d.pop("answered_from_public", UNSET)

        def _parse_answered_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        answered_name = _parse_answered_name(d.pop("answered_name", UNSET))

        def _parse_author_external_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        author_external_id = _parse_author_external_id(
            d.pop("author_external_id", UNSET)
        )

        def _parse_author_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        author_name = _parse_author_name(d.pop("author_name", UNSET))

        def _parse_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        text = _parse_text(d.pop("text", UNSET))

        withheld = d.pop("withheld", UNSET)

        group_line_response = cls(
            at=at,
            from_bot=from_bot,
            in_pod=in_pod,
            answered_from_public=answered_from_public,
            answered_name=answered_name,
            author_external_id=author_external_id,
            author_name=author_name,
            text=text,
            withheld=withheld,
        )

        group_line_response.additional_properties = d
        return group_line_response

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
