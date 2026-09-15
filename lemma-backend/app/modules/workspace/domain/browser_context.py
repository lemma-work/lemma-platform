"""Which browser a caller means, named once.

The answer used to be worked out independently wherever it was needed, and the
copies disagreed: a viewer opened a page in one session and attached the person
to another; a saved login was injected into a third that the agent's own
commands never selected. Each derivation was defensible on its own and the
feature did not work.

Two functions, and they are the whole of it. There were three purposes and a
`BrowserContext` dataclass here as well, carrying a sandbox, an endpoint and a
target id; it had no callers, and the invariant it was introduced to enforce
lived in this docstring while every caller went on choosing sessions for
itself. A comment is not an abstraction. What actually stopped the copies
disagreeing is that there is one place each name is spelled, so this is that
place and nothing more.

* `agent_session` -- the agent working. One per conversation, so two
  conversations do not read each other's cookies. The sandbox is per *person*,
  shared by every agent they run, and the session is the only thing between
  them.
A sign-in's browser is *not* named here. It is named for the site, and the
relay is what names it (`browser_relay.state.session_for_domain`) because the
relay is what has to find the profile on disk. Spelling it here as well was a
second derivation of one name -- the exact thing this module exists to prevent
-- and the copy had no production caller at all: everything passes the domain
and lets the relay answer.

Watching or driving names no session of its own either: a viewer joins one of
these, and which one is the caller's to say.

The names are *policy* and live here. A `target_id` is *fact* and comes back
from the relay, which is the only thing that knows what the browser has open.
"""

from __future__ import annotations

from uuid import UUID


#: The session the image's own tooling defaults to. Kept as the fallback for a
#: caller with no conversation -- a CLI, a test -- rather than as anybody's
#: normal answer.
DEFAULT_SESSION = "workspace"


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


__all__ = [
    "DEFAULT_SESSION",
    "agent_session",
]
