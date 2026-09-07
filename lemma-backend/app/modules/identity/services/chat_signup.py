"""Turn a proven email address into an account, a workspace and a linked identity.

The end of the chat onboarding ladder. By the time this runs the surface has
established two things it could not establish alone: which address this person
controls (a code they read in their own inbox), and which phone or handle sent
the message (signed by the platform). This is what happens with that pair.

Three outcomes, and the caller does not have to care which:

    - The address already has an account. Link the identity to it. This is the
      common case and the one the old "please sign up" reply was wrong about.
    - It has none. Make one, then link.
    - Either way, make sure they have somewhere to work.

The workspace step runs for an existing account too. Signing up creates a person
and nothing else, so an account can perfectly well exist with no organization --
and someone in that state is equally stuck whether they arrived through the app
or through WhatsApp.

**On making an account without a password.** Lemma's user id *is* the SuperTokens
user id -- the row is created by the sign-up override, not beside it -- so an
account has to be a real auth identity or it is one that can never sign in. The
only recipe configured is `emailpassword`, so this signs up with a password
nobody is told and nobody keeps. The person reaches the web app through the
ordinary reset flow, which is safe to offer precisely because the address was
just proven. If a passwordless recipe is ever added, this is the one function
that changes.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from app.core.helpers.identifiers import normalize_mobile_e164
from app.core.log.log import get_logger
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.services.first_workspace import (
    ProvisionedWorkspace,
    ensure_first_workspace,
)
from app.modules.identity.services.organization_service import OrganizationService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChatOnboarding:
    """Who this person turned out to be, and where they now are."""

    user_id: UUID
    workspace: ProvisionedWorkspace
    #: False when the address already had an account we linked to instead.
    account_created: bool


class ChatSignupError(RuntimeError):
    """The account could not be made, and the sender has to be told something."""


async def _create_account(email: str) -> UUID:
    """Sign the address up for real, and mark the email verified.

    Verified because it was: the person read a code we sent to that inbox and
    typed it back on a channel the platform signs. Leaving it unverified would
    make an account that cannot pass its own email gate, having proved the very
    thing that gate asks for.
    """
    from supertokens_python.recipe.emailpassword.asyncio import sign_up
    from supertokens_python.recipe.emailpassword.interfaces import SignUpOkResult

    # Never told to anyone and never stored: the reset flow is how this person
    # reaches the web app, and it is safe to offer because the address is proven.
    password = f"{secrets.token_urlsafe(24)}aA1!"
    result = await sign_up("public", email, password)
    if not isinstance(result, SignUpOkResult):
        raise ChatSignupError(f"could not create an account for {email}")

    user_id = UUID(result.user.id)
    await _mark_email_verified(str(result.user.id), email)
    return user_id


async def _mark_email_verified(supertokens_user_id: str, email: str) -> None:
    from supertokens_python.recipe.emailverification.asyncio import (
        create_email_verification_token,
        verify_email_using_token,
    )
    from supertokens_python.recipe.emailverification.interfaces import (
        CreateEmailVerificationTokenOkResult,
    )
    from supertokens_python.types import RecipeUserId

    created = await create_email_verification_token(
        "public", RecipeUserId(supertokens_user_id), email
    )
    if not isinstance(created, CreateEmailVerificationTokenOkResult):
        # Already verified is the only other answer, and it is the state we want.
        return
    await verify_email_using_token(
        "public", created.token, attempt_account_linking=False
    )


async def onboard_proven_email(
    uow,
    *,
    organization_service: OrganizationService,
    email: str,
    existing_user_id: UUID | None,
    full_name: str | None = None,
    mobile_number: str | None = None,
    telegram_username: str | None = None,
    arrived_through_organization_id: UUID | None = None,
) -> ChatOnboarding:
    """Link or create, then make sure there is somewhere to work.

    ``existing_user_id`` is the caller's own lookup for this address, passed in
    rather than repeated here so the decision to create is visible at the call
    site instead of buried behind a query.
    """
    account_created = False
    user_id = existing_user_id
    if user_id is None:
        user_id = await _create_account(email)
        account_created = True
        logger.info("identity.chat_signup.account_created", user_id=str(user_id))

    await _link_surface_identity(
        uow,
        user_id=user_id,
        full_name=full_name,
        mobile_number=mobile_number,
        telegram_username=telegram_username,
    )

    workspace = await ensure_first_workspace(
        uow,
        organization_service=organization_service,
        user_id=user_id,
        email=email,
        full_name=full_name,
        arrived_through_organization_id=arrived_through_organization_id,
    )
    return ChatOnboarding(
        user_id=user_id, workspace=workspace, account_created=account_created
    )


async def _link_surface_identity(
    uow,
    *,
    user_id: UUID,
    full_name: str | None,
    mobile_number: str | None,
    telegram_username: str | None,
) -> None:
    """Record the phone or handle that sent the message, so it is theirs next time.

    Marked verified, because the platform signed the payload it arrived in --
    the same proof the WhatsApp mobile-verification flow accepts.

    Nothing is overwritten. Someone whose profile already names a number chose
    that one, and a message from a second phone is not permission to replace it.
    """
    session = uow.session
    user = await session.get(User, user_id)
    if user is None:
        raise ChatSignupError("the account vanished between creating and linking it")

    if mobile_number and not user.mobile_number:
        # Platforms hand over their own shape -- Meta's `wa_id` is E.164 with the
        # `+` stripped -- and `normalize_mobile_e164` refuses a number with no
        # country code marker rather than guessing one. The digits are already
        # E.164, so the `+` is restored rather than inferred.
        digits = "".join(
            character for character in str(mobile_number) if character.isdigit()
        )
        if digits:
            user.mobile_number = normalize_mobile_e164(f"+{digits}")
            user.mobile_verified_at = datetime.now(timezone.utc)
    if telegram_username and not user.telegram_username:
        user.telegram_username = telegram_username.strip().lstrip("@").lower()
    if full_name and not user.first_name:
        first, _, last = full_name.strip().partition(" ")
        user.first_name = first or None
        user.last_name = last.strip() or None
    await session.flush()


__all__ = ["ChatOnboarding", "ChatSignupError", "onboard_proven_email"]
