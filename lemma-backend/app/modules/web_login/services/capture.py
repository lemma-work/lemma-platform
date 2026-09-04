"""Keeping the session somebody just created, so nobody is asked twice.

This is what turns a takeover from a one-off into a saved login. Without it the
person signs in, the agent gets its answer, the sandbox is released fifteen
minutes later, and the next run asks again — which is the experience the whole
feature exists to avoid.

What is captured is the browser's own session state, not a password. Nobody is
asked for a credential at any point: they signed in to the real site themselves,
and this reads what the browser now holds.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.web_login.domain.entities import WebLoginKind, WebLoginSecret
from app.modules.web_login.infrastructure.repository import WebLoginRepository
from app.modules.web_login.services.injection import (
    capture_command,
    looks_like_session_state,
    new_state_path,
)

logger = get_logger(__name__)

#: Session bundles are cookies and local storage, not documents. Well past what
#: a real one needs, and far under what a runaway page could produce.
_MAX_STATE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    saved: bool
    reason: str


async def capture_session(
    workspace_session,
    repository: WebLoginRepository,
    *,
    user_id: UUID,
    origin: str,
    label: str,
    conversation_id: UUID | None = None,
) -> CaptureOutcome:
    """Read the browser's current session and save it for this origin.

    Failure here is never fatal to the person's own login — they *are* signed in;
    what fails is only remembering it. So every path returns an outcome and says
    plainly whether they will be asked again.
    """
    path = new_state_path()
    result = await workspace_session.exec_command(cmd=capture_command(path), timeout=60)
    if not result.get("success") or result.get("exit_code") not in (0, None):
        return CaptureOutcome(
            False, "The browser would not export its session, so this was not saved."
        )

    try:
        raw = await workspace_session.read_file(path)
    finally:
        await workspace_session.exec_command(
            cmd=f"rm -f {shlex.quote(path)}", timeout=20
        )

    if len(raw) > _MAX_STATE_BYTES:
        return CaptureOutcome(
            False, "The exported session was implausibly large, so it was not saved."
        )

    state = raw.decode("utf-8", errors="replace")
    if not looks_like_session_state(state):
        # Storing an error message as somebody's login gives them an item that
        # looks fine in the list and fails every time it is used.
        return CaptureOutcome(
            False, "The browser exported nothing that looks like a session."
        )

    saved = await repository.save(
        user_id=user_id,
        origin=origin,
        label=label,
        kind=WebLoginKind.SESSION,
        secret=WebLoginSecret(state=state),
    )
    await repository.record(
        user_id=user_id,
        origin=origin,
        action="capture",
        outcome="ok",
        web_login_id=saved.id,
        conversation_id=conversation_id,
        detail="session saved after a takeover",
    )
    return CaptureOutcome(True, "Saved, so you will not be asked again for this site.")
