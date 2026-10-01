from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.rule_answer import RuleAnswer


T = TypeVar("T", bound="Rule")


@_attrs_define
class Rule:
    """A deterministic answer, tried before any engine.

    Either `when`, a JMESPath expression over the rendered state that answers
    when truthy, or `phrases`, exact matches against the text at `field` after
    lowercasing, collapsing whitespace and dropping trailing punctuation.

        Attributes:
            answer (RuleAnswer):
            field (str | Unset):  Default: 'text'.
            phrases (list[str] | None | Unset):
            when (None | str | Unset):
    """

    answer: RuleAnswer
    field: str | Unset = "text"
    phrases: list[str] | None | Unset = UNSET
    when: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        answer = self.answer.to_dict()

        field = self.field

        phrases: list[str] | None | Unset
        if isinstance(self.phrases, Unset):
            phrases = UNSET
        elif isinstance(self.phrases, list):
            phrases = self.phrases

        else:
            phrases = self.phrases

        when: None | str | Unset
        if isinstance(self.when, Unset):
            when = UNSET
        else:
            when = self.when

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "answer": answer,
            }
        )
        if field is not UNSET:
            field_dict["field"] = field
        if phrases is not UNSET:
            field_dict["phrases"] = phrases
        if when is not UNSET:
            field_dict["when"] = when

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.rule_answer import RuleAnswer

        d = dict(src_dict)
        answer = RuleAnswer.from_dict(d.pop("answer"))

        field = d.pop("field", UNSET)

        def _parse_phrases(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                phrases_type_0 = cast(list[str], data)

                return phrases_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(list[str] | None | Unset, data)

        phrases = _parse_phrases(d.pop("phrases", UNSET))

        def _parse_when(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        when = _parse_when(d.pop("when", UNSET))

        rule = cls(
            answer=answer,
            field=field,
            phrases=phrases,
            when=when,
        )

        return rule
