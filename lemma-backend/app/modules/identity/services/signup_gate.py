"""Who may create an account on this deployment.

A hosted deployment never had to ask. A Lemma Desktop installation is one
person's computer, and sharing it over the LAN or through a tunnel puts its
sign-up page in front of strangers -- so what happens at that page is a setting
(`SIGNUP_MODE`, which Desktop drives from its "who can join" choice) rather
than an accident of whoever found the address.

One rule sits in front of the mode: the first account on a deployment with no
accounts at all is always admitted. There is nobody yet who could have invited
it, and `invite_only` or `closed` would otherwise lock a fresh installation's
own user out of it. Nothing is recorded about that account; it is an ordinary
account from then on.

That check is a read, not a reservation, so two signups racing on an empty
database could both be admitted. It only matters where somebody other than the
first user can reach the sign-up page before the first account exists. On
Desktop that is not possible: the app and API listen on loopback only, sharing
(the LAN gateway or a tunnel) is the only way anybody else reaches them, and
Desktop onboarding creates the first account before sharing can be turned on.
A server deployment that wants a closed door from the first request should
create its first account before exposing the sign-up page.

`SignupGate.admit` is called by every path that can create a user: the
email/password sign-up API, the OAuth sign-in-up recipe, and the verified
email-code completion. A path that creates users without calling it is the
bug this module exists to prevent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.identity.config import IdentitySettings, identity_settings
from app.modules.identity.domain.errors import SignupNotAllowedError
from app.modules.identity.infrastructure.signup_store import SqlSignupStore

logger = get_logger(__name__)

SignupMode = Literal["open", "invite_only", "closed"]


class SignupStore(Protocol):
    async def has_any_user(self) -> bool: ...

    async def has_pending_invitation(
        self, email: str, *, now: datetime, invitation_id: UUID | None = None
    ) -> bool: ...


class Admission(StrEnum):
    """Why a signup was let in. Logged, so a surprising account can be explained."""

    FIRST_ACCOUNT = "first_account"
    OPEN = "open"
    INVITED = "invited"


class SignupGate:
    def __init__(
        self,
        *,
        settings: IdentitySettings = identity_settings,
        store: SignupStore | None = None,
    ) -> None:
        self._settings = settings
        self._store: SignupStore = store or SqlSignupStore()

    async def admit(
        self,
        email: str,
        *,
        invitation_id: str | None = None,
        email_proven: bool = True,
    ) -> Admission:
        """Admit a new account for `email`, or raise `SignupNotAllowedError`.

        `email` must already be normalised: it is compared with invitation
        addresses as given.

        `email_proven` is whether the address will have been shown to belong
        to the person before the account can be used: a provider vouched for
        it, a code was sent to it, or email verification is required. When it
        is not -- a Desktop installation shared with verification off -- an
        invitation "for this address" proves nothing, because anybody can type
        the address. Then the invitation itself must be presented: its id,
        from the link that was sent, which only the invitee was given.

        The mode is not consulted at all while it would admit anyone (`open`),
        so a hosted deployment pays no query per signup. Otherwise the
        first-account check runs before the mode, because on an empty
        deployment there is nobody who could have sent an invitation.
        """
        mode = self._settings.effective_signup_mode()
        if mode == "open":
            return Admission.OPEN
        if not await self._store.has_any_user():
            logger.info("identity.signup.admitted", admission=Admission.FIRST_ACCOUNT)
            return Admission.FIRST_ACCOUNT
        now = datetime.now(timezone.utc)
        presented = _invitation_uuid(invitation_id)
        invited = mode == "invite_only" and (
            (email_proven or presented is not None)
            and await self._store.has_pending_invitation(
                email, now=now, invitation_id=None if email_proven else presented
            )
        )
        if invited:
            logger.info("identity.signup.admitted", admission=Admission.INVITED)
            return Admission.INVITED
        code = (
            SignupNotAllowedError.INVITE_ONLY
            if mode == "invite_only"
            else SignupNotAllowedError.CLOSED
        )
        logger.info("identity.signup.refused", code=code, signup_mode=mode)
        raise SignupNotAllowedError(code)


def _invitation_uuid(raw: str | None) -> UUID | None:
    try:
        return UUID(raw.strip()) if raw else None
    except ValueError:
        return None


def get_signup_gate() -> SignupGate:
    return SignupGate()
