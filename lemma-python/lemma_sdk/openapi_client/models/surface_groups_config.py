from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SurfaceGroupsConfig")


@_attrs_define
class SurfaceGroupsConfig:
    """How the bot treats people outside the pod in its groups. Mirrored.

    Attributes:
        answers_outsiders (bool | Unset): Answer people outside the pod in this bot's groups, from what the pod made
            Public. Off, the bot answers only the pod's members, whatever a group's own switch says. Default: True.
    """

    answers_outsiders: bool | Unset = True

    def to_dict(self) -> dict[str, Any]:
        answers_outsiders = self.answers_outsiders

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if answers_outsiders is not UNSET:
            field_dict["answers_outsiders"] = answers_outsiders

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        answers_outsiders = d.pop("answers_outsiders", UNSET)

        surface_groups_config = cls(
            answers_outsiders=answers_outsiders,
        )

        return surface_groups_config
