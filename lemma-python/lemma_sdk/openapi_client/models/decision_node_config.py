from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.decision_node_question import DecisionNodeQuestion
    from ..models.decision_rule import DecisionRule


T = TypeVar("T", bound="DecisionNodeConfig")


@_attrs_define
class DecisionNodeConfig:
    """Configuration for Decision node.

    Attributes:
        question (DecisionNodeQuestion | None | Unset): Asked only when no rule matched: a decider's closed question,
            with a branch per option. The node needs rules, a question, or both.
        rules (list[DecisionRule] | Unset):
    """

    question: DecisionNodeQuestion | None | Unset = UNSET
    rules: list[DecisionRule] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.decision_node_question import DecisionNodeQuestion

        question: dict[str, Any] | None | Unset
        if isinstance(self.question, Unset):
            question = UNSET
        elif isinstance(self.question, DecisionNodeQuestion):
            question = self.question.to_dict()
        else:
            question = self.question

        rules: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.rules, Unset):
            rules = []
            for rules_item_data in self.rules:
                rules_item = rules_item_data.to_dict()
                rules.append(rules_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if question is not UNSET:
            field_dict["question"] = question
        if rules is not UNSET:
            field_dict["rules"] = rules

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decision_node_question import DecisionNodeQuestion
        from ..models.decision_rule import DecisionRule

        d = dict(src_dict)

        def _parse_question(data: object) -> DecisionNodeQuestion | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                question_type_0 = DecisionNodeQuestion.from_dict(data)

                return question_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(DecisionNodeQuestion | None | Unset, data)

        question = _parse_question(d.pop("question", UNSET))

        _rules = d.pop("rules", UNSET)
        rules: list[DecisionRule] | Unset = UNSET
        if _rules is not UNSET:
            rules = []
            for rules_item_data in _rules:
                rules_item = DecisionRule.from_dict(rules_item_data)

                rules.append(rules_item)

        decision_node_config = cls(
            question=question,
            rules=rules,
        )

        decision_node_config.additional_properties = d
        return decision_node_config

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
