"""Whether this is a Desktop installation, and whether a user owns it.

Published for host execution (docs/architecture/desktop-host-execution.md §2),
which is the one thing the installation owner can do that nobody else on the
same computer can. The answer is `SignupGate`'s, not a second implementation:
off Desktop there is no owner, and the table is not even read.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.identity.config import identity_settings
from app.modules.identity.services.installation import get_signup_gate


def is_desktop_installation() -> bool:
    return identity_settings.is_desktop_installation()


async def is_installation_owner(user_id: UUID) -> bool:
    return await get_signup_gate().is_installation_owner(user_id)


__all__ = ["is_desktop_installation", "is_installation_owner"]
