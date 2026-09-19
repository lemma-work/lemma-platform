"""What a browser login is, now that the browser is the one keeping it.

One person's own way back in to a site Lemma has no connector for. The same
idea as a connector account with a different mechanism, which is why the
authorization model copies `CONNECTOR_ACCOUNT` rather than inventing a
parallel one.

**Nothing here is a secret, because nothing is stored.** There used to be a
`WebLoginSecret` -- the cookies and local storage read back out of a browser,
encrypted, and rebuilt in a different browser later. The sandbox's browser
keeps its own profile in the durable home now, so the session lives where the
session has always belonged and these types describe it rather than hold it.

Never a password, then or now. `connectors-and-accounts.md` promises the
system "shall never ask them for their provider password", and a stored
password is a different and worse class of secret -- reused across sites, not
revocable without changing it everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WebLogin:
    """A site the browser is signed in to.

    Read from the browser each time it is asked for, not from a table. `since`
    is the oldest cookie the site has, which is the closest thing to "when did
    I sign in" that a browser can honestly answer, and `expires` the soonest
    one to lapse.
    """

    site: str
    cookie_count: int
    since: datetime | None = None
    expires: datetime | None = None


@dataclass(frozen=True, slots=True)
class PendingSignIn:
    """A sign-in somebody has been asked for and has not answered.

    Read from the paused tool call, not from a row: `origin` and `reason` are
    its arguments, and its being unresolved is what makes it pending. There used
    to be a table saying the same three things, and it drifted from the pause it
    described.
    """

    tool_call_id: str
    origin: str
    reason: str
    #: The protected page the agent was blocked on, when it named one.
    #: Verified instead of the origin root, both before asking and after
    #: answering -- see `SignInService.already_signed_in`.
    page_url: str | None = None


@dataclass(frozen=True, slots=True)
class SignInOutcome:
    """What came of a person answering.

    Returned to the page so it can say what happened, and carried to the agent
    on the approval's own payload. Not stored: the decision row is the record.

    `working` is what the site looked like straight afterwards -- whether it
    stopped asking for a login. Reported rather than enforced: the person has
    already done what was asked, and telling the agent "they say they signed
    in but the page still shows a form" is more use than refusing them.
    """

    origin: str
    signed_in: bool
    working: bool = False


__all__ = [
    "PendingSignIn",
    "SignInOutcome",
    "WebLogin",
]
