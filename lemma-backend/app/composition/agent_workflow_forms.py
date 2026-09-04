"""Turning what a person said into the values a workflow form is waiting for.

This is what an agent needs when somebody answers a form assignment in prose —
over email especially, where "yes, 40 units, PO-8812" is the whole reply and
nothing about it is shaped like a JSON object.

No new model does the extraction: the agent handling their reply already is
one. What it needs is the *schema* (rendered into its instructions by
``open_notifications``) and a way to submit that refuses a bad guess.
``WorkflowEngine.submit_form`` is that way — it re-checks the assignee, merges
schema defaults, and validates against the resolved schema stored on the wait.
So a hallucinated field is rejected server-side rather than written into a run.

Lives in ``composition`` because the agent module must not import ``workflow``;
the lazy imports keep that true. Same shape as
``agent_surfaces/contracts/notifications.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory


async def submit_workflow_form(
    *,
    run_id: UUID,
    node_id: str,
    inputs: dict[str, Any],
    requester_user_id: UUID,
) -> tuple[bool, str]:
    """``(submitted, message)``.

    Never raises for a *rejected* submission. The refusal is the useful part —
    it names the field that was wrong, which the agent can take back to the
    person and ask again. Raising would hand the model a traceback instead.
    """
    from app.modules.workflow.domain.errors import (
        WorkflowAccessDeniedError,
        WorkflowDomainError,
    )
    from app.modules.workflow.api.dependencies import build_workflow_engine

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        engine = build_workflow_engine(uow)
        try:
            await engine.submit_form(
                run_id,
                node_id,
                inputs,
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
