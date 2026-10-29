from __future__ import annotations

from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define

T = TypeVar("T", bound="NavigateCommand")


@_attrs_define
class NavigateCommand:
    """
    Attributes:
        command (Literal['navigate']):
        url (str):
    """

    command: Literal["navigate"]
    url: str

    def to_dict(self) -> dict[str, Any]:
        command = self.command

        url = self.url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "command": command,
                "url": url,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        command = cast(Literal["navigate"], d.pop("command"))
        if command != "navigate":
            raise ValueError(f"command must match const 'navigate', got '{command}'")

        url = d.pop("url")

        navigate_command = cls(
            command=command,
            url=url,
        )

        return navigate_command
