from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.multi_choice_question_options_type_0 import (
        MultiChoiceQuestionOptionsType0,
    )


T = TypeVar("T", bound="MultiChoiceQuestion")


@_attrs_define
class MultiChoiceQuestion:
    """Any number of the options, including none.

    Attributes:
        prompt (str):
        options (MultiChoiceQuestionOptionsType0 | None | Unset):
        type_ (Literal['multi_choice'] | Unset):  Default: 'multi_choice'.
    """

    prompt: str
    options: MultiChoiceQuestionOptionsType0 | None | Unset = UNSET
    type_: Literal["multi_choice"] | Unset = "multi_choice"

    def to_dict(self) -> dict[str, Any]:
        from ..models.multi_choice_question_options_type_0 import (
            MultiChoiceQuestionOptionsType0,
        )

        prompt = self.prompt

        options: dict[str, Any] | None | Unset
        if isinstance(self.options, Unset):
            options = UNSET
        elif isinstance(self.options, MultiChoiceQuestionOptionsType0):
            options = self.options.to_dict()
        else:
            options = self.options

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "prompt": prompt,
            }
        )
        if options is not UNSET:
            field_dict["options"] = options
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.multi_choice_question_options_type_0 import (
            MultiChoiceQuestionOptionsType0,
        )

        d = dict(src_dict)
        prompt = d.pop("prompt")

        def _parse_options(
            data: object,
        ) -> MultiChoiceQuestionOptionsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                options_type_0 = MultiChoiceQuestionOptionsType0.from_dict(data)

                return options_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(MultiChoiceQuestionOptionsType0 | None | Unset, data)

        options = _parse_options(d.pop("options", UNSET))

        type_ = cast(Literal["multi_choice"] | Unset, d.pop("type", UNSET))
        if type_ != "multi_choice" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'multi_choice', got '{type_}'")

        multi_choice_question = cls(
            prompt=prompt,
            options=options,
            type_=type_,
        )

        return multi_choice_question
