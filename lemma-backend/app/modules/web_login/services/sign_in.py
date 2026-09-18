"""Getting a run past a login wall.

Three calls, and between them they are the whole feature:

`already_signed_in` steers the browser to the site and looks at where it
landed. `open_request` puts the site in front of the person. `answer` lets the
waiting run carry on.

Notice what is *not* here any more. There is no capture, no scoping, no
encryption, no injection and no re-injection check, because there is nothing
to store: the sandbox's browser keeps its own profile in the durable home, so
a person who signs in stays signed in the way they do on their own machine.

That removed a whole category of defect rather than one instance of it. The
previous design read the browser's cookies out, guessed which of them
constituted "a login", encrypted that guess, and rebuilt it in a different
browser later -- then guessed again about whether the rebuild had worked. Both
guesses were wrong in production, in three different ways, and the last one
told a person their login had been kept when what had been kept was a cookie
banner's consent flag and a clock-skew number.

The question this asks now is the one a person would ask: open the page, and
see whether it is still asking you to sign in.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.web_login.domain.entities import PendingSignIn, SignInOutcome
from app.modules.web_login.services.origin import normalize_origin
from app.modules.web_login.services.pauses import (
    OwnerOfConversation,
    ReadPause,
    ResumePause,
    owner_through_contracts,
    pending_through_contracts,
    resume_through_approvals,
)
from app.modules.web_login.services.resolution import resolve_owner
from app.modules.web_login.services.sites import page_looks_like_a_login_wall
from sandbox_runtime.errors import SandboxCapabilityUnsupported

logger = get_logger(__name__)


class SignInService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        browser: object | None = None,
        resume: "ResumePause | None" = None,
        read_pause: "ReadPause | None" = None,
        owner_of: "OwnerOfConversation | None" = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._browser_override = browser
        self._browser_built: object | None = None
        #: How a finished sign-in reaches the run it paused. A named
        #: collaborator rather than a call this reaches for inside itself, so a
        #: test can stand in front of it -- a double placed *inside* the subject
        #: would certify the half that was not written.
        self._resume = resume or resume_through_approvals
        #: How it learns what is being waited on. Named for the same reason,
        #: and because it is the whole of what a sign-in request used to be: a
        #: row holding an origin, a reason and a status is three facts the
        #: paused tool call already has.
        self._read_pause = read_pause or pending_through_contracts
        #: Whose conversation this is. The ids in a sign-in URL are a lookup,
        #: not a credential, and this is the line that makes that true: without
        #: it, anybody holding a conversation id and a tool call id could read
        #: what site somebody else is being asked to sign in to, and answer for
        #: them.
        self._owner_of = owner_of or owner_through_contracts

    @property
    def _browser(self):
        """The browser, built on first use.

        Deferred because constructing it imports the whole workspace provider
        stack -- Docker, the E2B SDK, httpx -- and every process that merely
        registers these routes would pay for it at import.
        """
        if self._browser_override is not None:
            return self._browser_override
        if self._browser_built is None:
            from app.modules.workspace.contracts.browser import (
                browser_view_service,
            )

            self._browser_built = browser_view_service()()
        return self._browser_built

    async def close(self) -> None:
        # Only if one was ever built: closing must not be what constructs it.
        built = self._browser_override or self._browser_built
        if built is not None:
            await built.close()

    async def already_signed_in(
        self, *, origin: str, auth_ctx: Context | None = None
    ) -> bool:
        """Whether the browser can already reach this site signed in.

        Opens the page and reads where it landed. A site that wants a login
        sends the browser to a form and says so in the address or the title;
        one that does not, does not.

        The honest limits, because the previous version of this question
        claimed more than it could deliver. A site serving its login form at
        the same address under a neutral title reads as signed in here -- and
        that is the *safe* direction to be wrong, because the agent then meets
        the wall itself and calls this tool again, saying so. Being wrong the
        other way is what used to happen: reporting a working login and
        looping.

        An unreachable browser answers `False`. Not knowing is a reason to ask
        the person, not a reason to claim they are signed in.
        """
        owner = await resolve_owner(auth_ctx=auth_ctx)
        return await self._site_is_open(owner, normalize_origin(origin))

    async def _site_is_open(self, owner: UUID, site: str) -> bool:
        """The same question, for a caller that has already been authorised.

        `answer` reaches this rather than the public method above: it is
        already scoped by `pending`, which compares the conversation's owner
        against the caller, and resolving the permission a second time there
        would fail for want of a request context that a page's POST does not
        carry into the service.
        """
        try:
            landed = await self._browser.ensure_for_sign_in(
                owner, origin=site, report=True
            )
        except _relay_unavailable(), SandboxCapabilityUnsupported:
            logger.warning(
                "web_login.sign_in.browser_unreachable.degraded", origin=site
            )
            return False
        if not isinstance(landed, dict):
            return False
        where = f"{landed.get('url', '')} {landed.get('title', '')}"
        return not page_looks_like_a_login_wall(where)

    async def open_request(
        self,
        *,
        origin: str,
        reason: str,
        conversation_id: UUID | None,
        tool_call_id: str | None,
        auth_ctx: Context | None = None,
    ) -> str:
        """Put the site in front of the person, and say which origin it is.

        Writes nothing. The ask is already recorded -- the tool call that paused
        the run *is* the record, and it carries the origin and the reason. A row
        here said the same three things in a second place, and the two drifted.

        The browser is opened now rather than when they arrive so that the
        common case -- somebody who clicks straight away -- finds the site
        already loaded. It is best effort: by the time a person opens a link
        sent to their phone, the browser may well have retired for idleness,
        and the arrival re-opens it.
        """
        owner = await resolve_owner(auth_ctx=auth_ctx)
        site = normalize_origin(origin)
        del reason, tool_call_id, conversation_id  # carried by the pause

        try:
            await self._browser.ensure_for_sign_in(owner, origin=site)
        except _relay_unavailable(), SandboxCapabilityUnsupported:
            # Not fatal: the arrival opens the browser again. Logged because a
            # person landing on a cold browser waits, and knowing it started
            # cold is what explains the wait.
            logger.warning("web_login.sign_in.browser_not_ready.degraded")
        return site

    async def pending(
        self, *, conversation_id: UUID, user_id: UUID
    ) -> PendingSignIn | None:
        """What a sign-in link is for, read from the pause itself.

        There is no row to read. The paused tool call carries the origin and the
        reason the agent gave, and its still being unresolved is what "waiting"
        means -- so the three facts a sign-in page needs are the pause.

        `None` for a conversation somebody else owns, and this is the only
        place that check lives -- `answer` reaches the pause through here, so
        both routes are scoped by the one comparison.
        """
        async with self._uow_factory() as uow:
            owner = await self._owner_of(uow, conversation_id)
            if owner != user_id:
                # Deliberately indistinguishable from "nothing is waiting": the
                # caller is told this link is spent either way, and saying which
                # would tell a stranger that the conversation exists.
                return None
            paused = await self._read_pause(uow, conversation_id)
        if paused is None:
            return None
        origin = str(paused.tool_args.get("origin") or "")
        if not origin:
            return None
        return PendingSignIn(
            tool_call_id=paused.tool_call_id,
            origin=normalize_origin(origin),
            reason=str(paused.tool_args.get("reason") or ""),
        )

    async def answer(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str,
        user_id: UUID,
        signed_in: bool,
    ) -> SignInOutcome:
        """Let the waiting run carry on, and say what the site looks like now.

        One method for both answers because they are one answer: a person is
        telling us whether they signed in. It used to be `finish` and `decline`
        against a row with its own status, and the two drifted -- `decline`
        guarded against overwriting a resolved request and `finish` did not, so
        a stale tab could flip a declined sign-in to signed-in while the agent
        had already been told otherwise.

        Nothing guards that here because nothing can: the decision row is the
        lock, first writer wins, and this resolves through the same endpoint an
        approval button does.

        Nothing is stored. The browser holds the session, so "did it work" is a
        question about the browser and is answered by looking at it -- and the
        answer rides back to the agent rather than blocking the person, who has
        already done the thing they were asked to do.
        """
        found = await self.pending(conversation_id=conversation_id, user_id=user_id)
        if found is None or found.tool_call_id != tool_call_id:
            raise SignInNotPending(tool_call_id)

        site = found.origin
        working = await self._site_is_open(user_id, site) if signed_in else False
        await self._tell_the_agent(
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            user_id=user_id,
            approved=signed_in,
            working=working,
        )
        return SignInOutcome(origin=site, signed_in=signed_in, working=working)

    async def _tell_the_agent(
        self,
        *,
        conversation_id: UUID | None,
        tool_call_id: str | None,
        user_id: UUID,
        approved: bool,
        working: bool = False,
    ) -> None:
        """Resolve the paused tool call, carrying the outcome with it.

        `tool_call_id` is the approval id, so this goes through the same
        endpoint an approval button does -- idempotent, because the decision row
        is the double-submit lock, and self-healing, which is what makes it safe
        to call from a retry.

        `working` rides on the decision's `response`, which is the channel
        `ask_user` already uses for its answers.

        `approved` maps to APPROVE_ONCE, never APPROVE_FOR_SESSION: signing in
        once is not standing consent to be asked nothing next time. What makes
        the next run quiet is the browser still being signed in, which the
        person can see and undo.
        """
        if conversation_id is None or not tool_call_id:
            # A sign-in asked for outside a run -- from the CLI, or a test.
            # There is no pause to resolve and nothing has gone wrong.
            return

        async with self._uow_factory() as uow:
            reached = await self._resume(
                uow,
                conversation_id=conversation_id,
                tool_call_id=tool_call_id,
                user_id=user_id,
                approved=approved,
                response={"working": working},
            )
        if not reached:
            # The conversation is gone. The person still finished, and the
            # browser is still signed in -- so this is worth a line, not an
            # error thrown back at somebody who did what was asked of them.
            logger.warning(
                "web_login.sign_in.conversation_gone.degraded",
                conversation_id=str(conversation_id),
            )


def _relay_unavailable() -> type[Exception]:
    """The relay's own failure type, imported when it is needed.

    A function rather than a module-level import for the same reason the
    browser is: naming it at import time pulls the provider stack in.
    """
    from app.modules.workspace.contracts.browser import browser_unavailable

    return browser_unavailable()


class SignInNotPending(Exception):
    """Nothing is waiting on this answer.

    Either the run was never paused for this site, or somebody already answered.
    Both are the same fact from the page's side -- the link has been used -- and
    both used to be a row lookup returning a resolved status.
    """

    def __init__(self, tool_call_id: str) -> None:
        super().__init__(f"no sign-in is waiting on {tool_call_id}")
        self.tool_call_id = tool_call_id


__all__ = ["SignInNotPending", "SignInService", "page_looks_like_a_login_wall"]
