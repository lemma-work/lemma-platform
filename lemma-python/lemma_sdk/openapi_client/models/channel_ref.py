from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.channel_kind import ChannelKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="ChannelRef")


@_attrs_define
class ChannelRef:
    """
    Attributes:
        id (UUID):
        kind (ChannelKind):
        person (None | PersonRef | Unset):
        title (None | str | Unset):
    """

    id: UUID
    kind: ChannelKind
    person: None | PersonRef | Unset = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.person_ref import PersonRef

        id = str(self.id)

        kind = self.kind.value

        person: dict[str, Any] | None | Unset
        if isinstance(self.person, Unset):
            person = UNSET
        elif isinstance(self.person, PersonRef):
            person = self.person.to_dict()
        else:
            person = self.person

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "kind": kind,
            }
        )
        if person is not UNSET:
            field_dict["person"] = person
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        id = UUID(d.pop("id"))

        kind = ChannelKind(d.pop("kind"))

        def _parse_person(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                person_type_0 = PersonRef.from_dict(data)

                return person_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        person = _parse_person(d.pop("person", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        channel_ref = cls(
            id=id,
            kind=kind,
            person=person,
            title=title,
        )

        channel_ref.additional_properties = d
        return channel_ref

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
