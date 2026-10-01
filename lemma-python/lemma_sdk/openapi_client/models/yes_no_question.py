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

T = TypeVar("T", bound="YesNoQuestion")


@_attrs_define
class YesNoQuestion:
    """True or false. `yes` and `no` optionally say what each means.

    Attributes:
        prompt (str):
        no (None | str | Unset):
        type_ (Literal['yes_no'] | Unset):  Default: 'yes_no'.
        yes (None | str | Unset):
    """

    prompt: str
    no: None | str | Unset = UNSET
    type_: Literal["yes_no"] | Unset = "yes_no"
    yes: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        prompt = self.prompt

        no: None | str | Unset
        if isinstance(self.no, Unset):
            no = UNSET
        else:
            no = self.no

        type_ = self.type_

        yes: None | str | Unset
        if isinstance(self.yes, Unset):
            yes = UNSET
        else:
            yes = self.yes

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "prompt": prompt,
            }
        )
        if no is not UNSET:
            field_dict["no"] = no
        if type_ is not UNSET:
            field_dict["type"] = type_
        if yes is not UNSET:
            field_dict["yes"] = yes

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        prompt = d.pop("prompt")

        def _parse_no(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        no = _parse_no(d.pop("no", UNSET))

        type_ = cast(Literal["yes_no"] | Unset, d.pop("type", UNSET))
        if type_ != "yes_no" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'yes_no', got '{type_}'")

        def _parse_yes(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        yes = _parse_yes(d.pop("yes", UNSET))

        yes_no_question = cls(
            prompt=prompt,
            no=no,
            type_=type_,
            yes=yes,
        )

        return yes_no_question
