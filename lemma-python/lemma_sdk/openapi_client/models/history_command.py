from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.history_command_command import HistoryCommandCommand

T = TypeVar("T", bound="HistoryCommand")


@_attrs_define
class HistoryCommand:
    """
    Attributes:
        command (HistoryCommandCommand):
    """

    command: HistoryCommandCommand

    def to_dict(self) -> dict[str, Any]:
        command = self.command.value

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
        command = HistoryCommandCommand(d.pop("command"))

        history_command = cls(
            command=command,
        )

        return history_command
