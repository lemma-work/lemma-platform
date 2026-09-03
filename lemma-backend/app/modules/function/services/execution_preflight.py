"""What a run must satisfy before anything is persisted or a sandbox is leased.

Everything here is a pure function of a function definition and the input it was
called with. Split out of ``FunctionService`` rather than inlined because these
are the checks that decide whether a call becomes a *refusal* or a *run*, and
that distinction is what the caller sees: a run row, a lease, and most of the
time a cold sandbox start are all on the far side of them.
"""

from __future__ import annotations

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, best_match

from app.core.log.log import get_logger
from app.modules.function.domain.entities import FunctionEntity, FunctionStatus
from app.modules.function.domain.errors import FunctionValidationError
from app.modules.function.domain.types import JsonObject

logger = get_logger(__name__)


class LegacyFunctionRevisionRequired(Exception):
    """Internal control flow for a pre-artifact function definition.

    Deliberately not in ``domain/errors.py``: everything there is a
    ``DomainError`` carrying a status code, and this one never reaches a
    response -- ``FunctionUseCases`` catches it and compiles the revision.
    """

    def __init__(self, function: FunctionEntity):
        super().__init__(function.name)
        self.function = function


def require_ready_revision(function: FunctionEntity) -> None:
    """Refuse a call to a function with nothing runnable behind it."""
    if function.status != FunctionStatus.READY:
        raise FunctionValidationError("Function has no ready executable revision")
    if function.revision_hash is None:
        if function.code_path is not None:
            raise LegacyFunctionRevisionRequired(function)
        raise FunctionValidationError("Function has no ready executable revision")


def validate_input(function: FunctionEntity, input_data: JsonObject) -> None:
    """Refuse input that does not match what the function declared.

    Without this the mismatch is only found by ``revision.input_model(**input)``
    inside the sandbox, where it is a *failed run* rather than a refusal: a
    mistyped field name creates a run row, takes a worker lease, often pays a
    cold sandbox start, and answers 200 with a run that went
    PENDING -> RUNNING -> FAILED. That reads as "the function is broken" when it
    means "you called it wrong", and a JOB function does not even say that much
    until the caller polls.

    The schema is the one the platform derived from the function's own
    signature, so this is the same check the sandbox would make, made before
    anything is spent on it. A schema that is missing or that ``jsonschema``
    cannot compile is treated as no schema: refusing a call because *our*
    derivation is malformed would take a working function offline, and the
    sandbox still validates.
    """
    schema = function.input_schema
    if not schema:
        return
    try:
        # `check_schema`, not just construction: the constructor accepts
        # anything and only raises once it walks the instance, where a bad
        # schema surfaces as an unhandled `UnknownType` rather than a refusal
        # anyone can read.
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        logger.warning(
            "function.preflight.underivable_input_schema",
            function_id=str(function.id) if function.id else None,
        )
        return
    error = best_match(Draft202012Validator(schema).iter_errors(input_data))
    if error is None:
        return
    field = ".".join(str(part) for part in error.absolute_path) or "input"
    raise FunctionValidationError(
        f"Input for function '{function.name}' is invalid at '{field}': "
        f"{error.message}. Check the function's declared input and try again."
    )
