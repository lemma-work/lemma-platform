from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.decider_definition import DeciderDefinition


T = TypeVar("T", bound="DeciderResponse")


@_attrs_define
class DeciderResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        definition (DeciderDefinition): What a decider version is: every field an engine or a rule reads.
        id (UUID):
        name (str):
        updated_at (datetime.datetime):
        user_id (None | UUID):
        version (int):
        visibility (str):
        warnings (list[str] | Unset): Things the definition allows but that are usually a mistake.
    """

    created_at: datetime.datetime
    definition: DeciderDefinition
    id: UUID
    name: str
    updated_at: datetime.datetime
    user_id: None | UUID
    version: int
    visibility: str
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        definition = self.definition.to_dict()

        id = str(self.id)

        name = self.name

        updated_at = self.updated_at.isoformat()

        user_id: None | str
        if isinstance(self.user_id, UUID):
            user_id = str(self.user_id)
        else:
            user_id = self.user_id

        version = self.version

        visibility = self.visibility

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "definition": definition,
                "id": id,
                "name": name,
                "updated_at": updated_at,
                "user_id": user_id,
                "version": version,
                "visibility": visibility,
            }
        )
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decider_definition import DeciderDefinition

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        definition = DeciderDefinition.from_dict(d.pop("definition"))

        id = UUID(d.pop("id"))

        name = d.pop("name")

        updated_at = isoparse(d.pop("updated_at"))

        def _parse_user_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                user_id_type_0 = UUID(data)

                return user_id_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        user_id = _parse_user_id(d.pop("user_id"))

        version = d.pop("version")

        visibility = d.pop("visibility")

        warnings = cast(list[str], d.pop("warnings", UNSET))

        decider_response = cls(
            created_at=created_at,
            definition=definition,
            id=id,
            name=name,
            updated_at=updated_at,
            user_id=user_id,
            version=version,
            visibility=visibility,
            warnings=warnings,
        )

        decider_response.additional_properties = d
        return decider_response

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
