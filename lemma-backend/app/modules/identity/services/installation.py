"""Who may join this installation, and who owns it.

Two questions a hosted deployment never had to ask. A Lemma Desktop
installation is one person's computer; sharing it over the LAN or through a
tunnel puts its sign-up page in front of strangers, and the next change lets
the installation *owner* -- and nobody else -- run commands on the host rather
than in the VM. So the first account is recorded as the owner, exactly once,
and what happens at the sign-up page after that is a setting rather than an
accident of whoever found the address.

`SignupGate.admit` is called by every path that can create a user: the
email/password sign-up API, the OAuth sign-in-up recipe, and the verified
email-code completion. A path that creates users without calling it is the
bug this module exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.identity.config import IdentitySettings, identity_settings
from app.modules.identity.domain.errors import SignupNotAllowedError
from app.modules.identity.infrastructure.installation_owner_store import (
    SqlInstallationOwnerStore,
)

logger = get_logger(__name__)

SignupMode = Literal["open", "invite_only", "closed"]
DeploymentKind = Literal["server", "desktop"]


class InstallationOwnerStore(Protocol):
    async def reserve_first_signup(
        self, email: str, *, now: datetime, reservation_ttl: timedelta
    ) -> bool: ...

    async def owner_user_id(
        self, *, now: datetime, reservation_ttl: timedelta
    ) -> UUID | None: ...

    async def has_pending_invitation(self, email: str, *, now: datetime) -> bool: ...


class Admission(StrEnum):
    """Why a signup was let in. Logged, so a surprising account can be explained."""

    OWNER = "owner"
    OPEN = "open"
    INVITED = "invited"


@dataclass(frozen=True, slots=True)
class InstallationView:
    deployment: DeploymentKind
    is_owner: bool
    signup_mode: SignupMode


class SignupGate:
    def __init__(
        self,
        *,
        settings: IdentitySettings = identity_settings,
        store: InstallationOwnerStore | None = None,
    ) -> None:
        self._settings = settings
        self._store: InstallationOwnerStore = store or SqlInstallationOwnerStore()

    async def admit(self, email: str) -> Admission:
        """Admit a new account for `email`, or raise `SignupNotAllowedError`.

        `email` must already be normalised: it is compared with the
        reservation and with invitation addresses as given.

        The owner check runs before the mode, not after, because on a Desktop
        installation the first account has nobody who could have invited it --
        `invite_only` and `closed` would otherwise lock the owner out of their
        own computer.
        """
        now = datetime.now(timezone.utc)
        if self._settings.is_desktop_installation():
            reserved = await self._store.reserve_first_signup(
                email, now=now, reservation_ttl=self._reservation_ttl()
            )
            if reserved:
                logger.info("identity.signup.admitted", admission=Admission.OWNER)
                return Admission.OWNER
        mode = self._settings.effective_signup_mode()
        if mode == "open":
            return Admission.OPEN
        if mode == "invite_only" and await self._store.has_pending_invitation(
            email, now=now
        ):
            logger.info("identity.signup.admitted", admission=Admission.INVITED)
            return Admission.INVITED
        code = (
            SignupNotAllowedError.INVITE_ONLY
            if mode == "invite_only"
            else SignupNotAllowedError.CLOSED
        )
        logger.info("identity.signup.refused", code=code, signup_mode=mode)
        raise SignupNotAllowedError(code)

    async def is_installation_owner(self, user_id: UUID) -> bool:
        """Whether `user_id` owns this installation. Always False off Desktop.

        A hosted deployment has no owner in this sense -- its oldest account is
        just an account -- so the table is not consulted there at all, and a
        row that somehow existed could not grant anything.
        """
        if not self._settings.is_desktop_installation():
            return False
        owner = await self._store.owner_user_id(
            now=datetime.now(timezone.utc), reservation_ttl=self._reservation_ttl()
        )
        return owner is not None and owner == user_id

    def _reservation_ttl(self) -> timedelta:
        return timedelta(seconds=self._settings.installation_owner_reservation_seconds)

    async def view_for(self, user_id: UUID) -> InstallationView:
        return InstallationView(
            deployment=self._settings.deployment_kind,
            is_owner=await self.is_installation_owner(user_id),
            signup_mode=self._settings.effective_signup_mode(),
        )


def get_signup_gate() -> SignupGate:
    return SignupGate()
