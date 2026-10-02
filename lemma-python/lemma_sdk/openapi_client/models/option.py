from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="Option")


@_attrs_define
class Option:
    """What one option means, and optionally what it must not be used for.

    Attributes:
        description (str):
        examples (list[str] | Unset):
        not_for (None | str | Unset):
    """

    description: str
    examples: list[str] | Unset = UNSET
    not_for: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        examples: list[str] | Unset = UNSET
        if not isinstance(self.examples, Unset):
            examples = self.examples

        not_for: None | str | Unset
        if isinstance(self.not_for, Unset):
            not_for = UNSET
        else:
            not_for = self.not_for

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "description": description,
            }
        )
        if examples is not UNSET:
            field_dict["examples"] = examples
        if not_for is not UNSET:
            field_dict["not_for"] = not_for

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        examples = cast(list[str], d.pop("examples", UNSET))

        def _parse_not_for(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        not_for = _parse_not_for(d.pop("not_for", UNSET))

        option = cls(
            description=description,
            examples=examples,
            not_for=not_for,
        )

        return option
