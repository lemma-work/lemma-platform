"""The error a send raises when it has nothing to send with.

A send with no token, no line to send from or nobody to send to used to log at
debug and return. Whatever does not raise is recorded as delivered, so a run
finished "delivered" having sent nothing -- Slack, WhatsApp and Teams all did
this, while Telegram and Resend already raised. One helper so the four say it
the same way, and so the message names what is missing rather than the platform
reading as unconfigured when only one field was absent.

``AgentSurfaceError`` because that is what the delivery ladder and notification
delivery catch per channel; a bare ``ValueError`` escapes them.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError


def unsendable(platform: str, **required: object) -> AgentSurfaceValidationError:
    """Name every ``required`` value that is empty, as a catchable error."""
    missing = [name for name, value in required.items() if not value]
    return AgentSurfaceValidationError(
        f"{platform} send is missing: {', '.join(missing)}."
    )
