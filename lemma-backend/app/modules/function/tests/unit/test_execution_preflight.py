"""Input that does not match the declaration is refused, not executed.

PS-FUNC-001 asks for a refusal naming the bad part. Without one the mismatch is
only found by ``revision.input_model(**input)`` inside the sandbox, where it
becomes a *failed run*: a row, a worker lease, usually a cold sandbox start, and
a 200 carrying a run that went PENDING -> RUNNING -> FAILED. That reads as "the
function is broken" rather than "you called it wrong".
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionStatus,
)
from app.modules.function.domain.errors import FunctionValidationError
from app.modules.function.services.execution_preflight import validate_input


#: What the platform derives from a function signature: a pydantic
#: ``model_json_schema()``, which is what the row actually holds.
_GREETING_SCHEMA = {
    "type": "object",
    "title": "Input",
    "properties": {
        "recipient": {"type": "string", "title": "Recipient"},
        "times": {"type": "integer", "title": "Times", "default": 1},
    },
    "required": ["recipient"],
}


def _function(**overrides) -> FunctionEntity:
    values = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "user_id": uuid4(),
        "name": "greet",
        "status": FunctionStatus.READY,
        "input_schema": _GREETING_SCHEMA,
    }
    values.update(overrides)
    return FunctionEntity(**values)


def test_input_matching_the_declaration_passes() -> None:
    validate_input(_function(), {"recipient": "Ada", "times": 2})


def test_a_mistyped_field_name_is_refused_and_the_message_names_it() -> None:
    """The typo case: the declared field is missing, so nothing can run."""
    with pytest.raises(FunctionValidationError) as raised:
        validate_input(_function(), {"recipent": "Ada"})

    assert "greet" in raised.value.message
    assert "recipient" in raised.value.message
    assert raised.value.status_code < 500


def test_a_wrong_type_is_refused_and_the_message_names_the_field() -> None:
    with pytest.raises(FunctionValidationError) as raised:
        validate_input(_function(), {"recipient": "Ada", "times": "twice"})

    assert "times" in raised.value.message


def test_a_function_with_no_derived_schema_is_not_blocked() -> None:
    """Pre-derivation definitions still run; the sandbox is the backstop."""
    validate_input(_function(input_schema={}), {"anything": True})


def test_a_schema_we_cannot_compile_is_treated_as_no_schema() -> None:
    """Our own derivation being malformed must not take a function offline."""
    validate_input(_function(input_schema={"type": "not-a-type"}), {"recipient": 1})
