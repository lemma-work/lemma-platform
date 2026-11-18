from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from ..openapi_client.api.decisions import decision_make
from ..openapi_client.models.decision_response import DecisionResponse
from ..openapi_client.models.make_decision_request import MakeDecisionRequest
from .base import BoundResource


class PodDecisions(BoundResource):
    """Ask closed questions about some evidence. Nothing is stored.

    `schema` is a flat JSON Schema object, one property per question, its
    `description` the question. Each property is a choice, a multi-choice, a
    yes/no or a short integer scale::

        result = pod.decisions.make(
            instruction="Triage this support email for the billing team.",
            evidence={"subject": subject, "body": body},
            schema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["billing", "bug", "other"],
                        "description": "What is it about?",
                    },
                    "urgent": {"type": "boolean", "description": "Reply today?"},
                },
            },
        )
        answer = result.answers["category"]
        if answer.value is None:
            unsure = True  # the provider judged the evidence supports none
        elif answer.confidence is None:
            unsure = False  # no measure to compare: your call, per question
        else:
            unsure = answer.confidence < 0.7

    Treat an answer as unsure by that one rule, whichever provider the
    deployment runs: the language-model provider answers `None` when the
    evidence did not support an answer, and the `typesafe` provider always
    answers the value it judged most likely, with that value's probability as
    `confidence` where it reports one (for several choices, the confidence of
    the least certain per-option decision, not the probability of the whole
    selection). A value with no `confidence` carries no measure -- every
    answer of the language-model provider, and a `typesafe` scale read from its
    score alone -- so whether to act on it is yours to decide. The threshold is
    yours too, because what a wrong answer costs differs per question. A provider that could not answer raises instead (503 or 429),
    so "unsure" and "failed" never look alike.
    """

    def make(
        self,
        *,
        instruction: str,
        evidence: Any,
        schema: Mapping[str, Any],
        examples: Sequence[Mapping[str, Any]] | None = None,
        priority: Literal["interactive", "background"] = "background",
    ) -> DecisionResponse:
        body: dict[str, Any] = {
            "instruction": instruction,
            "evidence": evidence,
            "schema": dict(schema),
            "priority": priority,
        }
        if examples:
            body["examples"] = [dict(example) for example in examples]
        return self._call(
            decision_make,
            self._pod_uuid(),
            body=body,
            body_model=MakeDecisionRequest,
        )
