"""What `browser_sign_in` takes and gives back.

Everything else the browser can do is reached through `agent-browser` in the
workspace shell, so its request and result shapes are the CLI's own JSON rather
than models here.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.modules.agent.tools.context import BaseToolResponse


class BrowserSignInRequest(BaseModel):
    """Ask for a site to be signed in to, without ever handling a password."""

    origin: str = Field(
        description=(
            "The site to be signed in to, as a scheme and host — "
            "`https://app.example.com`. Not a full page URL."
        ),
    )
    reason: str = Field(
        description=(
            "What you are doing and why this site needs a login, in one "
            "sentence and in plain words. This is shown to the person as the "
            "explanation for why they are being asked, so write it for them "
            "rather than for a log."
        ),
        max_length=500,
    )


class BrowserSignInResponse(BaseToolResponse):
    outcome: str = Field(
        default="signed_in",
        description=(
            "`signed_in` the browser now holds a login for this site; "
            "`declined` the person said no — do the task another way or stop "
            "and say why; `expired` nobody answered and the conversation moved "
            "on; `error` the request could not be made."
        ),
    )
    origin: Optional[str] = Field(default=None, description="The site this concerned.")
    source: Optional[str] = Field(
        default=None,
        description=(
            "`saved` a stored session was loaded; `person` somebody signed in just now."
        ),
    )
    saved: bool = Field(
        default=False,
        description=(
            "Whether the login was kept for next time. False with "
            "`signed_in` means this run can carry on but the next one will ask "
            "again."
        ),
    )
