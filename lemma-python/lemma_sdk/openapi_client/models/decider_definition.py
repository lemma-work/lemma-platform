from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.decider_definition_questions import DeciderDefinitionQuestions
    from ..models.input_view import InputView
    from ..models.policy import Policy
    from ..models.rule import Rule


T = TypeVar("T", bound="DeciderDefinition")


@_attrs_define
class DeciderDefinition:
    """What a decider version is: every field an engine or a rule reads.

    Attributes:
        description (str):
        questions (DeciderDefinitionQuestions):
        guidance (None | str | Unset):
        input_ (InputView | Unset): The only part of the state any engine sees.

            `fields` are top-level keys or dotted paths into an object state. Leaving
            them out passes the whole state. Either way the rendered view is cut at
            `max_chars`, and the engines are told it was.
        policy (Policy | Unset): What each rung may answer, and when a rung passes a question on.
        rules (list[Rule] | Unset):
    """

    description: str
    questions: DeciderDefinitionQuestions
    guidance: None | str | Unset = UNSET
    input_: InputView | Unset = UNSET
    policy: Policy | Unset = UNSET
    rules: list[Rule] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        questions = self.questions.to_dict()

        guidance: None | str | Unset
        if isinstance(self.guidance, Unset):
            guidance = UNSET
        else:
            guidance = self.guidance

        input_: dict[str, Any] | Unset = UNSET
        if not isinstance(self.input_, Unset):
            input_ = self.input_.to_dict()

        policy: dict[str, Any] | Unset = UNSET
        if not isinstance(self.policy, Unset):
            policy = self.policy.to_dict()

        rules: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.rules, Unset):
            rules = []
            for rules_item_data in self.rules:
                rules_item = rules_item_data.to_dict()
                rules.append(rules_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "description": description,
                "questions": questions,
            }
        )
        if guidance is not UNSET:
            field_dict["guidance"] = guidance
        if input_ is not UNSET:
            field_dict["input"] = input_
        if policy is not UNSET:
            field_dict["policy"] = policy
        if rules is not UNSET:
            field_dict["rules"] = rules

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decider_definition_questions import DeciderDefinitionQuestions
        from ..models.input_view import InputView
        from ..models.policy import Policy
        from ..models.rule import Rule

        d = dict(src_dict)
        description = d.pop("description")

        questions = DeciderDefinitionQuestions.from_dict(d.pop("questions"))

        def _parse_guidance(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        guidance = _parse_guidance(d.pop("guidance", UNSET))

        _input_ = d.pop("input", UNSET)
        input_: InputView | Unset
        if isinstance(_input_, Unset):
            input_ = UNSET
        else:
            input_ = InputView.from_dict(_input_)

        _policy = d.pop("policy", UNSET)
        policy: Policy | Unset
        if isinstance(_policy, Unset):
            policy = UNSET
        else:
            policy = Policy.from_dict(_policy)

        _rules = d.pop("rules", UNSET)
        rules: list[Rule] | Unset = UNSET
        if _rules is not UNSET:
            rules = []
            for rules_item_data in _rules:
                rules_item = Rule.from_dict(rules_item_data)

                rules.append(rules_item)

        decider_definition = cls(
            description=description,
            questions=questions,
            guidance=guidance,
            input_=input_,
            policy=policy,
            rules=rules,
        )

        return decider_definition
