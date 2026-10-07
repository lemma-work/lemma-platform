from __future__ import annotations

import json
from types import SimpleNamespace

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import decisions

SCHEMA = {
    "type": "object",
    "properties": {"urgent": {"type": "boolean", "description": "Reply today?"}},
}
ANSWER = {
    "answers": {"urgent": {"value": True, "confidence": None}},
    "provider": "model",
    "model": "fast",
    "usage": {"input_tokens": 10, "output_tokens": 3},
}


class FakeDecisions:
    def __init__(self) -> None:
        self.asked: list[dict] = []

    def make(self, **request):
        self.asked.append(request)
        return ANSWER


def _client(fake: FakeDecisions):
    class FakeClient:
        def pod(self, pod_id):
            return SimpleNamespace(decisions=fake)

    return FakeClient()


def test_flags_build_the_request_and_the_answer_is_printed(
    runner, patch_run, json_state
):
    fake = FakeDecisions()
    patch_run(decisions, client=_client(fake), state=json_state)

    result = runner.invoke(
        app,
        [
            "decision",
            "run",
            "-i",
            "Is it urgent?",
            "-e",
            "Server down",
            "--schema",
            json.dumps(SCHEMA),
            "--priority",
            "interactive",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert fake.asked == [
        {
            "instruction": "Is it urgent?",
            "evidence": "Server down",
            "schema": SCHEMA,
            "priority": "interactive",
        }
    ]
    assert json.loads(result.stdout)["answers"]["urgent"]["value"] is True


def test_a_whole_request_comes_from_a_file_and_flags_override_it(
    runner, patch_run, json_state, tmp_path
):
    fake = FakeDecisions()
    patch_run(decisions, client=_client(fake), state=json_state)
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "instruction": "Old instruction",
                "evidence": {"subject": "Refund"},
                "schema": SCHEMA,
                "examples": [{"evidence": "x", "answers": {"urgent": False}}],
            }
        )
    )

    result = runner.invoke(
        app, ["decisions", "run", "-f", str(request), "-i", "New instruction"]
    )

    assert result.exit_code == 0, result.stderr
    asked = fake.asked[0]
    assert asked["instruction"] == "New instruction"
    assert asked["evidence"] == {"subject": "Refund"}
    assert asked["examples"] == [{"evidence": "x", "answers": {"urgent": False}}]


def test_evidence_and_schema_can_come_from_files(
    runner, patch_run, json_state, tmp_path
):
    fake = FakeDecisions()
    patch_run(decisions, client=_client(fake), state=json_state)
    evidence = tmp_path / "event.json"
    evidence.write_text(json.dumps({"subject": "Server down"}))
    schema = tmp_path / "questions.json"
    schema.write_text(json.dumps(SCHEMA))

    result = runner.invoke(
        app,
        [
            "decision",
            "run",
            "-i",
            "Is it urgent?",
            "--evidence-file",
            str(evidence),
            "--schema-file",
            str(schema),
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert fake.asked[0]["evidence"] == {"subject": "Server down"}
    assert fake.asked[0]["schema"] == SCHEMA


def test_evidence_can_be_piped_in(runner, patch_run, json_state):
    fake = FakeDecisions()
    patch_run(decisions, client=_client(fake), state=json_state)

    result = runner.invoke(
        app,
        ["decision", "run", "-i", "Urgent?", "-e", "-", "--schema", json.dumps(SCHEMA)],
        input="The whole site is down\n",
    )

    assert result.exit_code == 0, result.stderr
    assert fake.asked[0]["evidence"] == "The whole site is down\n"


def test_a_missing_part_is_a_usage_error_before_any_request(
    runner, patch_run, json_state
):
    fake = FakeDecisions()
    patch_run(decisions, client=_client(fake), state=json_state)

    result = runner.invoke(app, ["decision", "run", "-i", "Urgent?", "-e", "x"])

    assert result.exit_code == 2
    assert "schema" in result.stderr
    assert fake.asked == []


def test_a_bad_priority_or_unknown_field_is_refused(runner, patch_run, json_state):
    fake = FakeDecisions()
    patch_run(decisions, client=_client(fake), state=json_state)
    base = ["decision", "run", "-i", "x", "-e", "y", "--schema", json.dumps(SCHEMA)]

    bad_priority = runner.invoke(app, [*base, "--priority", "urgent"])
    unknown = runner.invoke(
        app,
        [
            "decision",
            "run",
            "-d",
            json.dumps(
                {"instruction": "x", "evidence": "y", "schema": SCHEMA, "subject": "z"}
            ),
        ],
    )

    assert bad_priority.exit_code == 2
    assert unknown.exit_code == 2
    assert "subject" in unknown.stderr
    assert fake.asked == []


def test_a_priority_that_is_not_a_string_is_a_usage_error(
    runner, patch_run, json_state
):
    fake = FakeDecisions()
    patch_run(decisions, client=_client(fake), state=json_state)
    payload = {"instruction": "x", "evidence": "y", "schema": SCHEMA}

    for priority in ([], {}):
        result = runner.invoke(
            app,
            ["decision", "run", "-d", json.dumps({**payload, "priority": priority})],
        )
        assert result.exit_code == 2, result.stderr
        assert "--priority" in result.stderr
    assert fake.asked == []
