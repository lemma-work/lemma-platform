"""What another module may use from `web_login`.

Reaching into the module's internals would make a caller's build depend on where
inside `web_login` each thing happens to live. Same shape as
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
from app.modules.web_login.services.origin import InvalidOrigin, normalize_origin

__all__ = [
    "InvalidOrigin",
    "WebLogin",
    "WebLoginKind",
    "WebLoginNotFound",
    "WebLoginRepository",
    "WebLoginSecret",
    "normalize_origin",
]
