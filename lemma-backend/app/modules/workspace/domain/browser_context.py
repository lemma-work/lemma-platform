"""Which browser, in which sandbox, on which page -- answered once.

This exists because the answer used to be worked out independently wherever it
was needed, and the copies disagreed. A viewer opened a page in one browser
session and attached the person to another; a login was injected into a third
that the agent's own commands never selected. Each derivation was defensible on
its own and the feature did not work.

So: a purpose goes in, a `BrowserContext` comes out, and everything downstream
reads it rather than deriving anything. The three purposes are the three reasons
anyone touches this browser, and they differ in exactly one way -- which session
they belong in:

* ``AGENT`` -- the agent working. One session per conversation, so two
  conversations do not read each other's cookies. The sandbox is per *person*,
  shared by every agent they run, and the session is the only thing between them.
* ``SIGN_IN`` -- a person signing in to one site. A session named for that site,
  so what a later capture can possibly contain is decided by where the sign-in
  happened rather than by a filter somebody has to remember to apply afterwards.
* ``VIEW`` -- watching or driving. Never names a session of its own: it joins one
  of the other two, and which one is the caller's to say.

The session name is *policy* and lives here. The `target_id` is *fact* and comes
back from the relay, which is the only thing that knows what the browser has
open -- see `BrowserViewService.resolve`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.modules.workspace.domain.sandbox import SandboxHandle
from app.modules.workspace.providers.base import SandboxEndpoint

#: The session the image's own tooling defaults to. Kept as the fallback for a
#: caller with no conversation -- a CLI, a test -- rather than as anybody's
#: normal answer.
DEFAULT_SESSION = "workspace"


class BrowserPurpose(StrEnum):
    AGENT = "agent"
    SIGN_IN = "sign_in"
    VIEW = "view"


def agent_session(conversation_id: UUID | None) -> str:
    """The session one conversation's agent browses in.

    Per conversation rather than per person, because the sandbox is already per
    person: without this, an agent in one pod inherits every cookie an agent in
    another pod picked up, including a saved login the person granted for a
    different task entirely. `agent-browser --session` is a whole separate
    browser with its own profile, which is the isolation we want and already
    have the mechanism for.
    """
    if conversation_id is None:
        return DEFAULT_SESSION
    return f"conv-{conversation_id.hex}"


def login_session(domain: str) -> str:
    """The session a sign-in to one site happens in.

    Defined by the relay, because the relay is what names the profile directory
    on disk; imported rather than re-spelled so the two cannot drift.
    """
    from sandbox_runtime.browser_relay.state import session_for_domain

    return session_for_domain(domain)


@dataclass(frozen=True, slots=True)
class BrowserContext:
    """One resolved answer to "which browser, and which page in it".

    Frozen because it is a decision already taken. Anything that wants a
    different session or a different page resolves again rather than editing
    this, which is what keeps "the session the page was opened in" and "the
    session the socket attaches to" the same value rather than two values that
    are usually equal.
    """

    user_id: UUID
    purpose: BrowserPurpose
    #: Which sandbox, at which epoch. Carried so a caller acting on this cannot
    #: silently act on a sandbox that has since been replaced.
    sandbox: SandboxHandle
    #: How to reach it, and whether that address is on the internet. `public` is
    #: what the sign-in path checks before putting anybody's session into it.
    endpoint: SandboxEndpoint
    #: The browser session, as the relay reported having used it.
    session: str
    #: The page, as the relay reported it. `None` before a browser is ensured.
    target_id: str | None = None
    #: Where that page currently is, for showing a person what they are on.
    url: str | None = None

    @property
    def is_public(self) -> bool:
        """Whether this sandbox's ports answer the internet behind only a token."""
        return self.endpoint.public


__all__ = [
    "DEFAULT_SESSION",
    "BrowserContext",
    "BrowserPurpose",
    "agent_session",
    "login_session",
]
