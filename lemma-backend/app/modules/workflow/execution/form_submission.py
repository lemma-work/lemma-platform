"""Form submission checks: is this person allowed to submit, and is the data valid.

Split out of ``WorkflowEngine``, which was over the size limit and had these two
sitting in it as pure functions of their arguments — neither touches engine state
beyond the unit of work.
"""

from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, best_match

from app.core.log.log import get_logger
from app.modules.workflow.domain.errors import (
    WorkflowAccessDeniedError,
    WorkflowDomainError,
)
from app.modules.workflow.domain.wait import WorkflowRunWaitEntity

logger = get_logger(__name__)


class FormNodeMismatchError(WorkflowDomainError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="WORKFLOW_FORM_NODE_MISMATCH",
            status_code=422,
        )


class FormValidationError(WorkflowDomainError):
    """Submitted form inputs failed validation against the resolved schema."""

    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="WORKFLOW_FORM_VALIDATION_FAILED",
            status_code=422,
        )


def validate_form_inputs(
    node_id: str, schema: Any | None, data: Dict[str, Any]
) -> None:
    """Validate submitted form values against the resolved schema stored on
    the wait. A malformed schema (already validated at suspend) is treated
    as no-schema rather than blocking the user."""
    if not isinstance(schema, dict) or not schema:
        return
    try:
        # `check_schema`, not construction: the constructor accepts a malformed
        # schema without complaint, and the failure then surfaces from
        # `iter_errors` as `UnknownType` — which is not a `SchemaError`, so it
        # escaped this handler and reached the person submitting the form as an
        # unhandled error. That is the opposite of what the docstring promises.
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        logger.warning("workflow.form.invalid_schema", node_id=node_id)
        return
    validator = Draft202012Validator(schema)
    error = best_match(validator.iter_errors(data))
    if error is not None:
        field = ".".join(str(part) for part in error.absolute_path) or "input"
        raise FormValidationError(
            f"Form input for node '{node_id}' is invalid at '{field}': {error.message}"
        )


async def check_assignee(
    uow,
    wait: WorkflowRunWaitEntity,
    pod_id: UUID,
    requester_user_id: UUID | None,
) -> None:
    if wait.assigned_pod_member_id is None or requester_user_id is None:
        return
    from app.modules.pod.contracts.members import pod_member_id

    member_id = await pod_member_id(uow, pod_id, requester_user_id)
    if member_id is None or member_id != wait.assigned_pod_member_id:
        raise WorkflowAccessDeniedError(
            "Workflow wait is assigned to another pod member"
        )
