"""Recognising a structured final answer inside an Agent Host event stream.

ACP tool-call events carry no tool *name* — ``ToolCall`` has a ``title`` the
agent wrote for humans and a ``toolCallId``, and nothing that reliably says
"this was ``lemma_final_answer``". So the answer is recognised by the marker the
tool stamps into its own result, which rides in both the text block and the
structuredContent of the MCP result and therefore survives whichever half an
adapter echoes back.

The text fallback here is a deliberate last resort for an agent that never
called the tool. It is fenced hard, because guessing wrong invents a structured
result the run never produced: the *whole* message must be the JSON, and it must
satisfy the agent's schema.
"""

from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.final_answer.final_answer_toolset import (
    FINAL_ANSWER_MARKER,
    FINAL_ANSWER_TOOL_NAME,
)

FINAL_ANSWER_STATUSES = frozenset({"COMPLETED", "FAILED", "WAITING"})

# A fenced block that IS the whole message, e.g. ```json\n{...}\n```
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(?P<body>.*?)\s*```\Z", re.DOTALL)


def final_answer_record(value: object) -> JsonObject | None:
    """Recognise a final-answer payload by the marker the tool stamps on it."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except TypeError, ValueError:
            return None
    if isinstance(value, dict) and value.get(FINAL_ANSWER_MARKER) is True:
        return dict(value)
    return None


def final_answer_metadata(record: JsonObject) -> JsonObject:
    """The message metadata a final answer contributes.

    Mirrors what the in-process harness stamps, so ``RunMessageWriter`` reads
    both harnesses' terminal messages the same way.
    """
    metadata: JsonObject = {
        "structured_output": record.get("output"),
        "final_answer_tool_name": FINAL_ANSWER_TOOL_NAME,
    }
    status = record.get("status")
    if isinstance(status, str) and status.upper() in FINAL_ANSWER_STATUSES:
        metadata["final_answer_status"] = status.upper()
    if record.get("error"):
        metadata["final_answer_error"] = record["error"]
    if record.get("schema_violation"):
        metadata["final_answer_schema_violation"] = record["schema_violation"]
    if record.get("inferred"):
        # Makes "the tool path is failing on adapter X" visible in the data.
        metadata["final_answer_inferred"] = True
    return metadata


def infer_final_answer(
    message: str,
    *,
    output_schema: JsonObject | None,
) -> JsonObject | None:
    """Read a final answer out of the agent's own final text, or return None.

    Only when the *whole* trimmed message is one JSON object (or one fenced json
    block that is the whole message). Deliberately not a brace-slice of the
    text: that is what turns "here's an example: {...}" into a fabricated final
    answer.
    """
    parsed = _whole_message_json(message)
    if parsed is None:
        return None

    status = parsed.get("status")
    if isinstance(status, str) and status.upper() in FINAL_ANSWER_STATUSES:
        record: JsonObject = {
            FINAL_ANSWER_MARKER: True,
            "status": status.upper(),
            "output": parsed.get("output"),
            "error": parsed.get("error"),
        }
    else:
        # A bare object: treat it as the output, and invent no lifecycle status —
        # let the terminal event decide whether the run succeeded.
        record = {FINAL_ANSWER_MARKER: True, "output": parsed}

    if not _matches_schema(output_schema, record.get("output")):
        return None
    record["inferred"] = True
    return record


def _whole_message_json(message: str) -> JsonObject | None:
    """Parse the message iff the entire message is one JSON object."""
    text = message.strip()
    fenced = _FENCED_JSON.match(text)
    if fenced is not None:
        text = fenced.group("body").strip()
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        parsed = json.loads(text)
    except TypeError, ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _matches_schema(schema: JsonObject | None, output: object) -> bool:
    """Does an inferred output satisfy the agent's schema? No schema = accept."""
    if not schema:
        return True
    try:
        Draft202012Validator(schema).validate(output)
    except ValidationError:
        return False
    except SchemaError:
        # An uncompilable stored schema cannot judge anything, so it must not be
        # the reason a good answer is thrown away.
        return True
    return True


__all__ = [
    "FINAL_ANSWER_STATUSES",
    "final_answer_metadata",
    "final_answer_record",
    "infer_final_answer",
]
