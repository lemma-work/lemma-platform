from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.tab_command_command import TabCommandCommand

T = TypeVar("T", bound="TabCommand")


@_attrs_define
class TabCommand:
    """
    Attributes:
        command (TabCommandCommand):
        target (str):
    """

    command: TabCommandCommand
    target: str

    def to_dict(self) -> dict[str, Any]:
        command = self.command.value

        target = self.target

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "command": command,
                "target": target,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        command = TabCommandCommand(d.pop("command"))

        target = d.pop("target")

        tab_command = cls(
            command=command,
            target=target,
        )

        return tab_command
