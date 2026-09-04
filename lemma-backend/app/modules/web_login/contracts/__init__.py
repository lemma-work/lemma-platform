"""What another module may use from `web_login`.

`agent` needs a handful of these to implement `browser_login`, and reaching into
the module's internals to get them would make its build depend on where inside
`web_login` each thing happens to live. Same shape as
`workspace/contracts/tooling.py`, and for the same reason.

Note what is deliberately *not* here: nothing that returns a decrypted secret.
`WebLoginRepository.reveal_secret` exists, but a caller has to go through this
module's own service to reach it, so "who can decrypt a saved login" stays a
question with a short answer.
"""

from __future__ import annotations

from app.modules.web_login.domain.entities import (
    WebLogin,
    WebLoginKind,
    WebLoginSecret,
)
from app.modules.web_login.infrastructure.repository import (
    WebLoginNotFound,
    WebLoginRepository,
)
from app.modules.web_login.services.capture import CaptureOutcome, capture_session
from app.modules.web_login.services.injection import (
    InjectionOutcome,
    capture_command,
    current_totp,
    inject_web_login,
    looks_like_session_state,
    new_state_path,
)
from app.modules.web_login.services.origin import InvalidOrigin, normalize_origin

__all__ = [
    "CaptureOutcome",
    "InjectionOutcome",
    "InvalidOrigin",
    "WebLogin",
    "WebLoginKind",
    "WebLoginNotFound",
    "WebLoginRepository",
    "WebLoginSecret",
    "capture_command",
    "capture_session",
    "current_totp",
    "inject_web_login",
    "looks_like_session_state",
    "new_state_path",
    "normalize_origin",
]
