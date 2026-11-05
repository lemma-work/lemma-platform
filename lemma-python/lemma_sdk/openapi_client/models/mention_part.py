from __future__ import annotations

from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.mention_part_kind import MentionPartKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="MentionPart")


@_attrs_define
class MentionPart:
    """Somebody the line names, resolved: a member, a contact or an agent.

    Attributes:
        id (UUID):
        kind (MentionPartKind):
        type_ (Literal['mention']):
        name (None | str | Unset):
    """

    id: UUID
    kind: MentionPartKind
    type_: Literal["mention"]
    name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = str(self.id)

        kind = self.kind.value

        type_ = self.type_

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "kind": kind,
                "type": type_,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = UUID(d.pop("id"))

        kind = MentionPartKind(d.pop("kind"))

        type_ = cast(Literal["mention"], d.pop("type"))
        if type_ != "mention":
            raise ValueError(f"type must match const 'mention', got '{type_}'")

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        mention_part = cls(
            id=id,
            kind=kind,
            type_=type_,
            name=name,
        )

        mention_part.additional_properties = d
        return mention_part

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
