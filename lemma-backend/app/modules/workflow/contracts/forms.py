"""Answering a workflow form on behalf of the person a run is waiting on.

One operation, for the case where somebody replies in prose -- over email
especially, where "yes, 40 units, PO-8812" is the whole message and nothing
about it is shaped like a JSON object. No new model does the extraction: the
agent handling their reply already is one. What it needs is the *schema*
(rendered into its instructions by `open_notifications`) and a way to submit
that refuses a bad guess.

`WorkflowEngine.submit_form` is that way -- it re-checks the assignee, merges
schema defaults, and validates against the resolved schema stored on the wait --
so a hallucinated field is rejected here rather than written into a run. That is
the whole reason this is published from `workflow` rather than reimplemented by
the caller: the validation and the assignee check are this module's, and a
contract that handed back an engine would let a caller skip both.

Replaces `app/composition/agent_workflow_forms.py`, which lived outside both
modules only so that `agent` need not import `workflow`.

A submodule rather than `contracts/__init__`: this reaches the execution layer,
and `contracts/__init__` is imported by anything that wants any contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory


async def submit_workflow_form(
    *,
    run_id: UUID,
    node_id: str,
    inputs: dict[str, object],
    requester_user_id: UUID,
) -> tuple[bool, str]:
    """``(submitted, message)``.

    Its own unit of work, like the surfaces contracts an agent tool reaches
    beside it: a form submission moves a workflow on, and it is not part of the
    asking run's transaction -- if that run later fails and rolls back, the
    workflow has already stepped.

    Never raises for a *rejected* submission. The refusal is the useful part --
    it names the field that was wrong, which the agent can take back to the
    person and ask again. Raising would hand the model a traceback instead.
    """
    from app.modules.workflow.api.dependencies import build_workflow_engine
    from app.modules.workflow.domain.errors import (
        WorkflowAccessDeniedError,
        WorkflowDomainError,
    )

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        engine = build_workflow_engine(uow)
        try:
            await engine.submit_form(
                run_id,
                node_id,
                dict(inputs),
                requester_user_id=requester_user_id,
            )
        except WorkflowAccessDeniedError:
            # Deliberately not echoed: which form is assigned to whom is not
            # this caller's to learn.
            return False, "This form is not assigned to you."
        except WorkflowDomainError as exc:
            # The common base for FormValidationError (names the bad field),
            # FormNodeMismatchError, and WorkflowConflictError (already
            # submitted, or the run moved on). All are answers, not faults.
            return False, str(exc)
        except ValueError as exc:
            # The engine raises plain ValueError for a missing run or flow.
            return False, str(exc)
        await uow.commit()

    return True, "Submitted. The workflow has moved on to the next step."


__all__ = ["submit_workflow_form"]
