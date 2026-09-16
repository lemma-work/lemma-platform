"""What other modules may use from `web_login`.

Operations, not repositories. The previous version of this file said so and did
the opposite: it exported `WebLoginRepository`, whose constructor takes nothing
but a session and whose `reveal_secret` returns a person's cookies in plaintext.
Any module could have reached it, and the docstring claiming otherwise was the
only thing suggesting they could not.

So the rule is the shape of this file rather than a sentence in it. Nothing
below returns a decrypted secret, and nothing below can move a sign-in's state:
the one caller outside this module that needs to know about a paused sign-in
gets a read, and the one that needs to resolve one goes through the service,
which is what writes the audit trail.
"""

from __future__ import annotations

from app.modules.web_login.domain.entities import (
    PendingSignIn,
    SignInOutcome,
)
from app.modules.web_login.services.origin import InvalidOrigin, normalize_origin
from app.modules.web_login.services.sign_in import (
    NotSignedInYet,
    SignInNotPending,
    SignInService,
)

__all__ = [
    "InvalidOrigin",
    "NotSignedInYet",
    "PendingSignIn",
    "SignInNotPending",
    "SignInOutcome",
    "SignInService",
    "normalize_origin",
]
