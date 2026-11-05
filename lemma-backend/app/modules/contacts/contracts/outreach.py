"""What the pod may send a contact, and how a contact stops it.

``contacts`` owns outreach policy; every module that writes to a contact asks
here first. The rules and their order are in ``contacts.domain.outreach``:
opt-out first, then a handle, then the platform's window, then (member lines
only) the contact's day.

* ``may_send`` -- the check, over the database only. Never counts; safe in any
  unit of work, and the answer a member can be given before a line is posted.
* ``count_against_today`` -- a member's line against the contact's day, in
  Redis, once per line. Called by whatever sends the line, with no unit of
  work open.
* ``opt_out`` / ``opt_out_by_token`` / ``heard_from`` -- STOP, the email link,
  and the person writing again.
* ``unsubscribe_headers`` -- ``List-Unsubscribe`` and one-click for an email
  carrying ``Verdict.unsubscribe_token``; the token travels to the mail
  adapter in the send's metadata under ``UNSUBSCRIBE_TOKEN_KEY``.

The caller supplies the platform's facts -- which kind of handle a place
reaches, and its window (``SendWindow``) -- because only the platform's
adapter knows them.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.contacts.contracts import contact_handles

from app.modules.contacts.domain.handles import (
    host_handle,
    host_issuer,
    is_start_request,
    is_stop_request,
)
from app.modules.contacts.domain.outreach import (
    Address,
    OutreachKind,
    RefusalCode,
    SendWindow,
    Verdict,
)
from app.modules.contacts.infrastructure.suppressions import SuppressionReason
from app.modules.contacts.services.outreach import (
    count_against_today,
    heard_from,
    may_send,
    opt_out,
    opt_out_by_token,
    opted_out_handles,
)
from app.modules.contacts.services.unsubscribe import (
    UnsubscribeTarget,
    read_unsubscribe_token,
    unsubscribe_headers,
    unsubscribe_link,
)


async def opted_out(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> bool:
    """Whether a handle of this contact asked the pod to stop writing to it --
    what a member is shown on the contact's channel."""
    handles = await contact_handles(uow, pod_id=pod_id, contact_id=contact_id)
    return bool(await opted_out_handles(uow, handles, pod_id=pod_id))


#: The send metadata key an email's unsubscribe token travels under, from the
#: delivery that asked ``may_send`` to the mail adapter that writes headers.
UNSUBSCRIBE_TOKEN_KEY = "contact_unsubscribe_token"

__all__ = [
    "UNSUBSCRIBE_TOKEN_KEY",
    "Address",
    "OutreachKind",
    "RefusalCode",
    "SendWindow",
    "SuppressionReason",
    "UnsubscribeTarget",
    "Verdict",
    "count_against_today",
    "heard_from",
    "host_handle",
    "host_issuer",
    "is_start_request",
    "is_stop_request",
    "may_send",
    "opt_out",
    "opt_out_by_token",
    "opted_out",
    "opted_out_handles",
    "read_unsubscribe_token",
    "unsubscribe_headers",
    "unsubscribe_link",
]
