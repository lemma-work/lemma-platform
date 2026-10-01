from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.decider_definition import DeciderDefinition
    from ..models.decision_node_question_branches import DecisionNodeQuestionBranches
    from ..models.decision_node_question_input_type_1 import (
        DecisionNodeQuestionInputType1,
    )
    from ..models.expression_input_binding import ExpressionInputBinding
    from ..models.literal_input_binding import LiteralInputBinding


T = TypeVar("T", bound="DecisionNodeQuestion")


@_attrs_define
class DecisionNodeQuestion:
    """A closed question the node asks when none of its rules matched.

    Answered by the decisions module -- the decider's own rules, then System
    One, then the system model -- and recorded once per step, so a resumed or
    retried step reads the answer it already has.

        Attributes:
            input_ (DecisionNodeQuestionInputType1 | ExpressionInputBinding | LiteralInputBinding): What the decision is
                about: one binding, or named bindings that become an object. Only the decider's input view of it reaches an
                engine.
            branches (DecisionNodeQuestionBranches | Unset): Option key to the id of the node that answer goes to. An answer
                with no branch falls through to the default outgoing edge.
            decider (None | str | Unset): A pod decider's name, or `system:<name>` for one that ships with Lemma. Leave out
                to ask `definition` inline.
            definition (DeciderDefinition | None | Unset): A decider asked inline: exactly one `choice` question. Nothing is
                learned for it; name a pod decider for answers people can correct.
            on_open (None | str | Unset): The node to go to when no rung could answer. Without it an open question takes its
                fallback option's branch, or falls through.
            question_key (None | str | Unset): The question to branch on. Leave out when the decider asks one.
    """

    input_: (
        DecisionNodeQuestionInputType1 | ExpressionInputBinding | LiteralInputBinding
    )
    branches: DecisionNodeQuestionBranches | Unset = UNSET
    decider: None | str | Unset = UNSET
    definition: DeciderDefinition | None | Unset = UNSET
    on_open: None | str | Unset = UNSET
    question_key: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.decider_definition import DeciderDefinition
        from ..models.expression_input_binding import ExpressionInputBinding
        from ..models.literal_input_binding import LiteralInputBinding

        input_: dict[str, Any]
        if isinstance(self.input_, ExpressionInputBinding):
            input_ = self.input_.to_dict()
        elif isinstance(self.input_, LiteralInputBinding):
            input_ = self.input_.to_dict()
        else:
            input_ = self.input_.to_dict()

        branches: dict[str, Any] | Unset = UNSET
        if not isinstance(self.branches, Unset):
            branches = self.branches.to_dict()

        decider: None | str | Unset
        if isinstance(self.decider, Unset):
            decider = UNSET
        else:
            decider = self.decider

        definition: dict[str, Any] | None | Unset
        if isinstance(self.definition, Unset):
            definition = UNSET
        elif isinstance(self.definition, DeciderDefinition):
            definition = self.definition.to_dict()
        else:
            definition = self.definition

        on_open: None | str | Unset
        if isinstance(self.on_open, Unset):
            on_open = UNSET
        else:
            on_open = self.on_open

        question_key: None | str | Unset
        if isinstance(self.question_key, Unset):
            question_key = UNSET
        else:
            question_key = self.question_key

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "input": input_,
            }
        )
        if branches is not UNSET:
            field_dict["branches"] = branches
        if decider is not UNSET:
            field_dict["decider"] = decider
        if definition is not UNSET:
            field_dict["definition"] = definition
        if on_open is not UNSET:
            field_dict["on_open"] = on_open
        if question_key is not UNSET:
            field_dict["question_key"] = question_key

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decider_definition import DeciderDefinition
        from ..models.decision_node_question_branches import (
            DecisionNodeQuestionBranches,
        )
        from ..models.decision_node_question_input_type_1 import (
            DecisionNodeQuestionInputType1,
        )
        from ..models.expression_input_binding import ExpressionInputBinding
        from ..models.literal_input_binding import LiteralInputBinding

        d = dict(src_dict)

        def _parse_input_(
            data: object,
        ) -> (
            DecisionNodeQuestionInputType1
            | ExpressionInputBinding
            | LiteralInputBinding
        ):
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_type_0_type_0 = ExpressionInputBinding.from_dict(data)

                return input_type_0_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_type_0_type_1 = LiteralInputBinding.from_dict(data)

                return input_type_0_type_1
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            if not isinstance(data, dict):
                raise TypeError()
            input_type_1 = DecisionNodeQuestionInputType1.from_dict(data)

            return input_type_1

        input_ = _parse_input_(d.pop("input"))

        _branches = d.pop("branches", UNSET)
        branches: DecisionNodeQuestionBranches | Unset
        if isinstance(_branches, Unset):
            branches = UNSET
        else:
            branches = DecisionNodeQuestionBranches.from_dict(_branches)

        def _parse_decider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        decider = _parse_decider(d.pop("decider", UNSET))

        def _parse_definition(data: object) -> DeciderDefinition | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                definition_type_0 = DeciderDefinition.from_dict(data)

                return definition_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(DeciderDefinition | None | Unset, data)

        definition = _parse_definition(d.pop("definition", UNSET))

        def _parse_on_open(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        on_open = _parse_on_open(d.pop("on_open", UNSET))

        def _parse_question_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        question_key = _parse_question_key(d.pop("question_key", UNSET))

        decision_node_question = cls(
            input_=input_,
            branches=branches,
            decider=decider,
            definition=definition,
            on_open=on_open,
            question_key=question_key,
        )

        decision_node_question.additional_properties = d
        return decision_node_question

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
