from __future__ import annotations

import json

import pytest

from .helpers import cli, cli_json

pytestmark = pytest.mark.e2e

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["billing", "bug", "other"],
            "description": "What is this support email about?",
        },
        "urgent": {"type": "boolean", "description": "Does it need a reply today?"},
        "severity": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "description": "How bad, 1 cosmetic to 3 blocking?",
        },
    },
}


def test_a_decision_comes_back_with_one_typed_answer_per_question(
    backend_server, test_user, test_pod
):
    payload = cli_json(
        [
            "decision",
            "run",
            "-i",
            "Triage this support email.",
            "-e",
            "I was charged twice this month, please refund one.",
            "--schema",
            json.dumps(SCHEMA),
        ],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=test_pod["id"],
    )

    answers = payload["answers"]
    assert set(answers) == {"category", "urgent", "severity"}
    assert answers["category"]["value"] in {"billing", "bug", "other", None}
    assert answers["urgent"]["value"] in {True, False, None}
    assert answers["severity"]["value"] in {1, 2, 3, None}
    assert payload["provider"] == "model"


def test_questions_outside_the_subset_are_refused_with_the_reason(
    backend_server, test_user, test_pod
):
    result = cli(
        [
            "decisions",
            "run",
            "-i",
            "Summarise this.",
            "-e",
            "A long email.",
            "--schema",
            json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Summary?"}
                    },
                }
            ),
        ],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=test_pod["id"],
    )

    assert result.exit_code == 1
    assert "schema.properties.summary" in result.stderr, result.stderr
