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
    from ..models.choice_question_options_type_0 import ChoiceQuestionOptionsType0


T = TypeVar("T", bound="ChoiceQuestion")


@_attrs_define
class ChoiceQuestion:
    """Exactly one of the options.

    Declared options are the fixed ones; a caller adds its own with each call.
    That is how one definition answers "which of these?" over the person's
    pods, the open conversations or the options of a question already put to
    them, while its `none` or `not_an_answer` is always there to fall back on.

        Attributes:
            prompt (str):
            fallback (None | str | Unset): The option to answer when nothing clearly applies.
            options (ChoiceQuestionOptionsType0 | None | Unset):
            type_ (Literal['choice'] | Unset):  Default: 'choice'.
    """

    prompt: str
    fallback: None | str | Unset = UNSET
    options: ChoiceQuestionOptionsType0 | None | Unset = UNSET
    type_: Literal["choice"] | Unset = "choice"

    def to_dict(self) -> dict[str, Any]:
        from ..models.choice_question_options_type_0 import ChoiceQuestionOptionsType0

        prompt = self.prompt

        fallback: None | str | Unset
        if isinstance(self.fallback, Unset):
            fallback = UNSET
        else:
            fallback = self.fallback

        options: dict[str, Any] | None | Unset
        if isinstance(self.options, Unset):
            options = UNSET
        elif isinstance(self.options, ChoiceQuestionOptionsType0):
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
        if fallback is not UNSET:
            field_dict["fallback"] = fallback
        if options is not UNSET:
            field_dict["options"] = options
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.choice_question_options_type_0 import ChoiceQuestionOptionsType0

        d = dict(src_dict)
        prompt = d.pop("prompt")

        def _parse_fallback(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        fallback = _parse_fallback(d.pop("fallback", UNSET))

        def _parse_options(data: object) -> ChoiceQuestionOptionsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                options_type_0 = ChoiceQuestionOptionsType0.from_dict(data)

                return options_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(ChoiceQuestionOptionsType0 | None | Unset, data)

        options = _parse_options(d.pop("options", UNSET))

        type_ = cast(Literal["choice"] | Unset, d.pop("type", UNSET))
        if type_ != "choice" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'choice', got '{type_}'")

        choice_question = cls(
            prompt=prompt,
            fallback=fallback,
            options=options,
            type_=type_,
        )

        return choice_question
