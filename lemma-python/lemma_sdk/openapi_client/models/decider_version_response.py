from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

if TYPE_CHECKING:
    from ..models.decider_definition import DeciderDefinition


T = TypeVar("T", bound="DeciderVersionResponse")


@_attrs_define
class DeciderVersionResponse:
    """
    Attributes:
        created_at (datetime.datetime):
        created_by (None | UUID):
        definition (DeciderDefinition): What a decider version is: every field an engine or a rule reads.
        version (int):
    """

    created_at: datetime.datetime
    created_by: None | UUID
    definition: DeciderDefinition
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        created_by: None | str
        if isinstance(self.created_by, UUID):
            created_by = str(self.created_by)
        else:
            created_by = self.created_by

        definition = self.definition.to_dict()

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "created_by": created_by,
                "definition": definition,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decider_definition import DeciderDefinition

        d = dict(src_dict)
        created_at = isoparse(d.pop("created_at"))

        def _parse_created_by(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_by_type_0 = UUID(data)

                return created_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | UUID, data)

        created_by = _parse_created_by(d.pop("created_by"))

        definition = DeciderDefinition.from_dict(d.pop("definition"))

        version = d.pop("version")

        decider_version_response = cls(
            created_at=created_at,
            created_by=created_by,
            definition=definition,
            version=version,
        )

        decider_version_response.additional_properties = d
        return decider_version_response

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
