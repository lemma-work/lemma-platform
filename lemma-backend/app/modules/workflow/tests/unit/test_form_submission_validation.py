"""What `validate_form_inputs` does with a schema it cannot use.

A form's schema is validated when the run suspends, so a malformed one reaching
the submit path means something upstream already went wrong. The deliberate
choice is that the person answering the form is not the one who pays for it:
they submit, and the value is accepted unchecked, rather than being handed an
error about a schema they never wrote and cannot fix.
"""

import pytest

from app.modules.workflow.execution.form_submission import (
    FormValidationError,
    validate_form_inputs,
)


def test_a_valid_submission_passes() -> None:
    schema = {"type": "object", "properties": {"amount": {"type": "integer"}}}

    validate_form_inputs("approve", schema, {"amount": 5})


def test_a_submission_that_breaks_the_schema_is_refused_by_field() -> None:
    schema = {"type": "object", "properties": {"amount": {"type": "integer"}}}

    with pytest.raises(FormValidationError) as refusal:
        validate_form_inputs("approve", schema, {"amount": "five"})

    assert "amount" in str(refusal.value)


def test_a_malformed_schema_does_not_block_the_submission() -> None:
    """The regression: `Draft202012Validator(schema)` accepts a bad schema.

    The constructor validates nothing, so the failure surfaced later from
    `iter_errors` as `UnknownType` — not a `SchemaError`, so the handler that
    exists to swallow exactly this missed it and the person submitting the form
    got an unhandled error instead of an accepted answer.
    """
    validate_form_inputs("approve", {"type": "not-a-real-type"}, {"anything": 1})


@pytest.mark.parametrize("schema", [None, {}, [], "not-a-schema"])
def test_no_usable_schema_means_no_validation(schema: object) -> None:
    validate_form_inputs("approve", schema, {"anything": 1})
