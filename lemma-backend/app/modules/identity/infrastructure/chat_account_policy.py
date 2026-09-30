"""Which accounts a chat may resolve to, provision for, or match by phone.

One rule, read in three places that used to disagree with the deployment: the
mobile-number lookup, `ensure_chat_workspace` and `active_chat_user`. Each
demanded ``is_verified`` outright -- but ``is_verified`` is the *email* proof,
and a deployment with ``AUTH_EMAIL_VERIFICATION_REQUIRED=false`` (Lemma Desktop,
and self-hosts without mail) creates every password account unverified and has
no way to change that. There, chat refused every account the server itself
treats as fully signed up.

So email verification counts exactly where the server requires it. Hosted
Lemma requires it, and nothing changes there.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import ColumnElement, and_, true

from app.core.config import settings
from app.modules.identity.infrastructure.models.user_models import User


class _AccountFlags(Protocol):
    is_active: bool
    is_deleted: bool
    is_verified: bool


def email_verification_counts(required: bool | None = None) -> bool:
    """Whether an unverified email disqualifies an account on this deployment.

    ``required`` stands in for the setting when a caller already knows it.
    """
    if required is None:
        required = settings.auth_email_verification_required
    return bool(required)


def is_chat_account(
    user: _AccountFlags | None, *, required: bool | None = None
) -> bool:
    """Active, not deleted, and email-verified where the server requires it."""
    return (
        user is not None
        and bool(user.is_active)
        and not user.is_deleted
        and (bool(user.is_verified) or not email_verification_counts(required))
    )


def chat_account_clause(*, required: bool | None = None) -> ColumnElement[bool]:
    """`is_chat_account` as a SQL predicate on `User`."""
    return and_(
        User.is_active.is_(True),
        User.is_deleted.is_(False),
        User.is_verified.is_(True) if email_verification_counts(required) else true(),
    )
