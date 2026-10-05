"""A visitor's answers to a table form, added as one row.

The row is added by the member who looks after the form, with exactly their
permission to write that table -- the form adds nothing to it. If they have left
the pod or lost access, the form stops taking answers rather than borrowing
someone else's.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.web_forms import (
    DEFAULT_CONFIRMATION,
    FormSpec,
    form_values,
)
from app.modules.datastore.contracts.forms import (
    FormTableUnavailable,
    insert_form_row,
)
from app.modules.pod.contracts.members import pod_member_id

logger = get_logger(__name__)


class FormClosed(Exception):
    """The form cannot take answers now: nobody looks after it, or the insert failed."""


async def add_form_row(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    widget_id: UUID,
    form: FormSpec,
    owner: UUID | None,
    contact_id: UUID | None,
    answers: dict[str, object],
) -> str:
    """Add the row; return what to say to the visitor.

    Raises ``FormAnswerRefused`` for answers that do not fit, before anything
    is written, and :class:`FormClosed` when the row could not be added.
    """
    values = form_values(form, answers)
    async with uow_factory() as uow:
        if owner is None or await pod_member_id(uow, pod_id, owner) is None:
            raise FormClosed("Nobody looks after this form")
        ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=owner, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            await insert_form_row(
                uow,
                pod_id=pod_id,
                table_name=form.table,
                values=values,
                user_id=owner,
                ctx=ctx,
                contact_id=contact_id,
            )
        except FormTableUnavailable as exc:
            logger.info(
                "agent_surfaces.web_form.insert_refused.observed",
                widget_id=str(widget_id),
                reason=str(exc)[:200],
            )
            raise FormClosed(str(exc)) from exc
        finally:
            reset_current_context(token)
        await uow.commit()
    return form.confirmation or DEFAULT_CONFIRMATION
