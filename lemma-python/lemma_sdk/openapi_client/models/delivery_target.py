from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.delivery_target_kind import DeliveryTargetKind
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="DeliveryTarget")


@_attrs_define
class DeliveryTarget:
    """
    Attributes:
        kind (DeliveryTargetKind):
        person (None | PersonRef | Unset): The member a push went to.
        source_id (None | Unset | UUID):
    """

    kind: DeliveryTargetKind
    person: None | PersonRef | Unset = UNSET
    source_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.person_ref import PersonRef

        kind = self.kind.value

        person: dict[str, Any] | None | Unset
        if isinstance(self.person, Unset):
            person = UNSET
        elif isinstance(self.person, PersonRef):
            person = self.person.to_dict()
        else:
            person = self.person

        source_id: None | str | Unset
        if isinstance(self.source_id, Unset):
            source_id = UNSET
        elif isinstance(self.source_id, UUID):
            source_id = str(self.source_id)
        else:
            source_id = self.source_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
            }
        )
        if person is not UNSET:
            field_dict["person"] = person
        if source_id is not UNSET:
            field_dict["source_id"] = source_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        kind = DeliveryTargetKind(d.pop("kind"))

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

        def _parse_source_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                source_id_type_0 = UUID(data)

                return source_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | Unset | UUID, data)

        source_id = _parse_source_id(d.pop("source_id", UNSET))

        delivery_target = cls(
            kind=kind,
            person=person,
            source_id=source_id,
        )

        delivery_target.additional_properties = d
        return delivery_target

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
