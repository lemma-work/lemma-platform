"""What other modules may use from `web_login`.

Operations, not repositories. Two earlier versions of this file got that
wrong in opposite directions -- one exported a repository whose `reveal_secret`
returned a person's cookies in plaintext, and the docstring claiming otherwise
was the only thing suggesting it could not be reached.

There is no repository now, and no secret to reveal: the browser keeps its own
profile, so what this module offers is the sign-in *conversation* -- ask, read
what is pending, answer -- and nothing that can hand out a session.
"""

from __future__ import annotations

from app.modules.web_login.domain.entities import (
    PendingSignIn,
    SignInOutcome,
)
from app.modules.web_login.services.origin import InvalidOrigin, normalize_origin
from app.modules.web_login.services.sign_in import (
    SignInNotPending,
    SignInService,
)

__all__ = [
    "InvalidOrigin",
    "PendingSignIn",
    "SignInNotPending",
    "SignInOutcome",
    "SignInService",
    "normalize_origin",
]
