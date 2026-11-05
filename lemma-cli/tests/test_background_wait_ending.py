"""The ending `lemma chat` plays for a run that finished unwatched says what
the server's own `completed` says: which conversation, and what it answered."""

from __future__ import annotations

import json

from lemma_cli.cli_core.background_waits import _resumed_messages


class _Conversations:
    def messages(self, conversation_id, *, after_sequence=None, page_token=None):
        del conversation_id, after_sequence, page_token
        return {
            "items": [
                {
                    "role": "assistant",
                    "kind": "TEXT",
                    "text": "Both reports are in.",
                    "agent_run_id": "run-2",
                    "sequence": 9,
                }
            ]
        }


def _frames(replayed) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in replayed.iter_lines()
        if line.startswith("data: ")
    ]


def test_the_ending_names_the_conversation_and_carries_the_output() -> None:
    output = {"answer": "Both reports are in."}

    *_, ending = _frames(
        _resumed_messages(_Conversations(), "conv-1", 3, "IDLE", output=output)
    )

    assert ending["type"] == "completed"
    assert ending["agent_run_id"] == "run-2"
    assert ending["data"]["conversation_id"] == "conv-1"
    assert ending["data"]["output_data"] == output


def test_an_ending_with_no_output_says_so_by_leaving_it_out() -> None:
    *_, ending = _frames(_resumed_messages(_Conversations(), "conv-1", 3, "IDLE"))

    assert ending["data"]["conversation_id"] == "conv-1"
    assert "output_data" not in ending["data"]
