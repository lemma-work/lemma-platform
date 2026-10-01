from __future__ import annotations

from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ScaleQuestion")


@_attrs_define
class ScaleQuestion:
    """One of 2 to 10 ordered levels, lowest first. Answers with the level's index.

    Attributes:
        levels (list[str]):
        prompt (str):
        type_ (Literal['scale'] | Unset):  Default: 'scale'.
    """

    levels: list[str]
    prompt: str
    type_: Literal["scale"] | Unset = "scale"

    def to_dict(self) -> dict[str, Any]:
        levels = self.levels

        prompt = self.prompt

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "levels": levels,
                "prompt": prompt,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        levels = cast(list[str], d.pop("levels"))

        prompt = d.pop("prompt")

        type_ = cast(Literal["scale"] | Unset, d.pop("type", UNSET))
        if type_ != "scale" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'scale', got '{type_}'")

        scale_question = cls(
            levels=levels,
            prompt=prompt,
            type_=type_,
        )

        return scale_question
