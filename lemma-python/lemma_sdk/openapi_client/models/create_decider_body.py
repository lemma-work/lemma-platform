from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.decider_definition import DeciderDefinition


T = TypeVar("T", bound="CreateDeciderBody")


@_attrs_define
class CreateDeciderBody:
    """
    Attributes:
        definition (DeciderDefinition): What a decider version is: every field an engine or a rule reads.
        name (str):
    """

    definition: DeciderDefinition
    name: str

    def to_dict(self) -> dict[str, Any]:
        definition = self.definition.to_dict()

        name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "definition": definition,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decider_definition import DeciderDefinition

        d = dict(src_dict)
        definition = DeciderDefinition.from_dict(d.pop("definition"))

        name = d.pop("name")

        create_decider_body = cls(
            definition=definition,
            name=name,
        )

        return create_decider_body
