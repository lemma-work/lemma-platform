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
        result.answers["category"].value  # "billing", or None when unsure

    An answer of `None` means the evidence did not support one. A provider that
    could not answer raises instead (503, or 429 with a Retry-After), so
    "unsure" and "failed" never look alike.
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
