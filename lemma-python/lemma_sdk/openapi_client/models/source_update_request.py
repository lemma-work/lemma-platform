from __future__ import annotations

from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SourceUpdateRequest")


@_attrs_define
class SourceUpdateRequest:
    """Absent is unchanged; neither field can be cleared.

    Attributes:
        answers_outsiders (bool | None | Unset): Answer people outside the pod here, or stop.
        owner (Literal['me'] | None | Unset): `me`: answer for its outsiders yourself from now on.
    """

    answers_outsiders: bool | None | Unset = UNSET
    owner: Literal["me"] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        answers_outsiders: bool | None | Unset
        if isinstance(self.answers_outsiders, Unset):
            answers_outsiders = UNSET
        else:
            answers_outsiders = self.answers_outsiders

        owner: Literal["me"] | None | Unset
        if isinstance(self.owner, Unset):
            owner = UNSET
        else:
            owner = self.owner

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if answers_outsiders is not UNSET:
            field_dict["answers_outsiders"] = answers_outsiders
        if owner is not UNSET:
            field_dict["owner"] = owner

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_answers_outsiders(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        answers_outsiders = _parse_answers_outsiders(d.pop("answers_outsiders", UNSET))

        def _parse_owner(data: object) -> Literal["me"] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            owner_type_0 = cast(Literal["me"], data)
            if owner_type_0 != "me":
                raise ValueError(
                    f"owner_type_0 must match const 'me', got '{owner_type_0}'"
                )
            return owner_type_0
            return cast(Literal["me"] | None | Unset, data)

        owner = _parse_owner(d.pop("owner", UNSET))

        source_update_request = cls(
            answers_outsiders=answers_outsiders,
            owner=owner,
        )

        source_update_request.additional_properties = d
        return source_update_request

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
