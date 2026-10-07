from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.make_decision_request_priority import MakeDecisionRequestPriority
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.decision_example_body import DecisionExampleBody
    from ..models.make_decision_request_schema import MakeDecisionRequestSchema


T = TypeVar("T", bound="MakeDecisionRequest")


@_attrs_define
class MakeDecisionRequest:
    """
    Attributes:
        evidence (Any):
        instruction (str): What to judge and how, in your words. Trusted: it is the only part of the request the
            provider follows.
        schema (MakeDecisionRequestSchema): The questions, as a flat JSON Schema object: one property per question, its
            `description` the question. Each property is a choice (`{"type": "string", "enum": [...]}`, or `oneOf` of
            `{const, description}`), a multi-choice (`{"type": "array", "items": <choice>, "uniqueItems": true}`), yes or no
            (`{"type": "boolean"}`), or a scale (`{"type": "integer", "minimum": 1, "maximum": 5}`, or `oneOf` of described
            integer levels). Free text and open-ended numbers are not supported.
        examples (list[DecisionExampleBody] | Unset): Past cases and their answers, to steer the provider.
        priority (MakeDecisionRequestPriority | Unset): `interactive` when someone is waiting on the answer: a shorter
            deadline and no second attempt. `background` otherwise. Default: MakeDecisionRequestPriority.BACKGROUND.
    """

    evidence: Any
    instruction: str
    schema: MakeDecisionRequestSchema
    examples: list[DecisionExampleBody] | Unset = UNSET
    priority: MakeDecisionRequestPriority | Unset = (
        MakeDecisionRequestPriority.BACKGROUND
    )

    def to_dict(self) -> dict[str, Any]:
        evidence = self.evidence

        instruction = self.instruction

        schema = self.schema.to_dict()

        examples: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.examples, Unset):
            examples = []
            for examples_item_data in self.examples:
                examples_item = examples_item_data.to_dict()
                examples.append(examples_item)

        priority: str | Unset = UNSET
        if not isinstance(self.priority, Unset):
            priority = self.priority.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "evidence": evidence,
                "instruction": instruction,
                "schema": schema,
            }
        )
        if examples is not UNSET:
            field_dict["examples"] = examples
        if priority is not UNSET:
            field_dict["priority"] = priority

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.decision_example_body import DecisionExampleBody
        from ..models.make_decision_request_schema import MakeDecisionRequestSchema

        d = dict(src_dict)
        evidence = d.pop("evidence")

        instruction = d.pop("instruction")

        schema = MakeDecisionRequestSchema.from_dict(d.pop("schema"))

        _examples = d.pop("examples", UNSET)
        examples: list[DecisionExampleBody] | Unset = UNSET
        if _examples is not UNSET:
            examples = []
            for examples_item_data in _examples:
                examples_item = DecisionExampleBody.from_dict(examples_item_data)

                examples.append(examples_item)

        _priority = d.pop("priority", UNSET)
        priority: MakeDecisionRequestPriority | Unset
        if isinstance(_priority, Unset):
            priority = UNSET
        else:
            priority = MakeDecisionRequestPriority(_priority)

        make_decision_request = cls(
            evidence=evidence,
            instruction=instruction,
            schema=schema,
            examples=examples,
            priority=priority,
        )

        return make_decision_request
