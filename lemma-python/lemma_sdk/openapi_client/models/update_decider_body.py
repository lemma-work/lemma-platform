from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.decider_definition import DeciderDefinition


T = TypeVar("T", bound="UpdateDeciderBody")


@_attrs_define
class UpdateDeciderBody:
    """
    Attributes:
        definition (DeciderDefinition): What a decider version is: every field an engine or a rule reads.
    """

    definition: DeciderDefinition

    def to_dict(self) -> dict[str, Any]:
        definition = self.definition.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "definition": definition,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decider_definition import DeciderDefinition

        d = dict(src_dict)
        definition = DeciderDefinition.from_dict(d.pop("definition"))

        update_decider_body = cls(
            definition=definition,
        )

        return update_decider_body
