from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.decision_question_answer import DecisionQuestionAnswer
    from ..models.decision_question_example import DecisionQuestionExample
    from ..models.decision_question_routes import DecisionQuestionRoutes
    from ..models.expression_input_binding import ExpressionInputBinding
    from ..models.literal_input_binding import LiteralInputBinding


T = TypeVar("T", bound="DecisionQuestion")


@_attrs_define
class DecisionQuestion:
    """One closed question, asked about one piece of evidence, routed on.

    Attributes:
        answer (DecisionQuestionAnswer): The question, as one closed JSON Schema property whose `description` is the
            question: a choice (`{"type": "string", "enum": [...]}` or `oneOf` of `{const, description}`), yes or no
            (`{"type": "boolean"}`), or a scale (`{"type": "integer", "minimum": 1, "maximum": 5}` or `oneOf` of integer
            levels). A multi-choice is not routable and is refused. Example: {'description': 'What is this support email
            about?', 'enum': ['billing', 'bug', 'other'], 'type': 'string'}.
        evidence (ExpressionInputBinding | LiteralInputBinding): What to judge, as an input binding resolved against the
            run context -- an email, an event, a row. Never followed as instructions. Example: {'type': 'expression',
            'value': 'start.payload.email'}.
        instruction (str): What to judge and how, in the author's words. Trusted: it is the only part of the question
            the provider follows.
        examples (list[DecisionQuestionExample] | Unset): Past cases and their answers, to steer the provider.
        min_confidence (float | None | Unset): An answer whose confidence is below this counts as unsure. Ignored when
            the provider reports no confidence, as a language model does.
        routes (DecisionQuestionRoutes | Unset): Next node id by answer, the answer written as a string: `true` or
            `false` for yes or no, `3` for a scale level, the option itself for a choice. An answer with no route takes the
            node's default edge -- its first outgoing edge. Example: {'billing': 'refund', 'bug': 'file_bug'}.
        unsure_next_node_id (None | str | Unset): Where to go when the evidence does not support an answer, or the
            answer's confidence is below `min_confidence`. Unset, an unsure answer takes the default edge.
    """

    answer: DecisionQuestionAnswer
    evidence: ExpressionInputBinding | LiteralInputBinding
    instruction: str
    examples: list[DecisionQuestionExample] | Unset = UNSET
    min_confidence: float | None | Unset = UNSET
    routes: DecisionQuestionRoutes | Unset = UNSET
    unsure_next_node_id: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.expression_input_binding import ExpressionInputBinding

        answer = self.answer.to_dict()

        evidence: dict[str, Any]
        if isinstance(self.evidence, ExpressionInputBinding):
            evidence = self.evidence.to_dict()
        else:
            evidence = self.evidence.to_dict()

        instruction = self.instruction

        examples: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.examples, Unset):
            examples = []
            for examples_item_data in self.examples:
                examples_item = examples_item_data.to_dict()
                examples.append(examples_item)

        min_confidence: float | None | Unset
        if isinstance(self.min_confidence, Unset):
            min_confidence = UNSET
        else:
            min_confidence = self.min_confidence

        routes: dict[str, Any] | Unset = UNSET
        if not isinstance(self.routes, Unset):
            routes = self.routes.to_dict()

        unsure_next_node_id: None | str | Unset
        if isinstance(self.unsure_next_node_id, Unset):
            unsure_next_node_id = UNSET
        else:
            unsure_next_node_id = self.unsure_next_node_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "answer": answer,
                "evidence": evidence,
                "instruction": instruction,
            }
        )
        if examples is not UNSET:
            field_dict["examples"] = examples
        if min_confidence is not UNSET:
            field_dict["min_confidence"] = min_confidence
        if routes is not UNSET:
            field_dict["routes"] = routes
        if unsure_next_node_id is not UNSET:
            field_dict["unsure_next_node_id"] = unsure_next_node_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decision_question_answer import DecisionQuestionAnswer
        from ..models.decision_question_example import DecisionQuestionExample
        from ..models.decision_question_routes import DecisionQuestionRoutes
        from ..models.expression_input_binding import ExpressionInputBinding
        from ..models.literal_input_binding import LiteralInputBinding

        d = dict(src_dict)
        answer = DecisionQuestionAnswer.from_dict(d.pop("answer"))

        def _parse_evidence(
            data: object,
        ) -> ExpressionInputBinding | LiteralInputBinding:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                evidence_type_0 = ExpressionInputBinding.from_dict(data)

                return evidence_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            if not isinstance(data, dict):
                raise TypeError()
            evidence_type_1 = LiteralInputBinding.from_dict(data)

            return evidence_type_1

        evidence = _parse_evidence(d.pop("evidence"))

        instruction = d.pop("instruction")

        _examples = d.pop("examples", UNSET)
        examples: list[DecisionQuestionExample] | Unset = UNSET
        if _examples is not UNSET:
            examples = []
            for examples_item_data in _examples:
                examples_item = DecisionQuestionExample.from_dict(examples_item_data)

                examples.append(examples_item)

        def _parse_min_confidence(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        min_confidence = _parse_min_confidence(d.pop("min_confidence", UNSET))

        _routes = d.pop("routes", UNSET)
        routes: DecisionQuestionRoutes | Unset
        if isinstance(_routes, Unset):
            routes = UNSET
        else:
            routes = DecisionQuestionRoutes.from_dict(_routes)

        def _parse_unsure_next_node_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        unsure_next_node_id = _parse_unsure_next_node_id(
            d.pop("unsure_next_node_id", UNSET)
        )

        decision_question = cls(
            answer=answer,
            evidence=evidence,
            instruction=instruction,
            examples=examples,
            min_confidence=min_confidence,
            routes=routes,
            unsure_next_node_id=unsure_next_node_id,
        )

        return decision_question
