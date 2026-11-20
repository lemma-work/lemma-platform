from __future__ import annotations

from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define

T = TypeVar("T", bound="TabsCommand")


@_attrs_define
class TabsCommand:
    """
    Attributes:
        command (Literal['tabs']):
    """

    command: Literal["tabs"]

    def to_dict(self) -> dict[str, Any]:
        command = self.command

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "command": command,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        command = cast(Literal["tabs"], d.pop("command"))
        if command != "tabs":
            raise ValueError(f"command must match const 'tabs', got '{command}'")

        tabs_command = cls(
            command=command,
        )

        return tabs_command
