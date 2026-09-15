"""Getting a run past a login wall, and keeping what that produced.

Three things happen here, and they are deliberately separate calls rather than
one "log in" that does whatever is needed:

`try_saved_login` loads a stored session into the browser and says whether the
site accepted it. `open_request` records that a person is being asked, and puts
the site in front of them. `finish` captures what they signed in to and stores
it, scoped to that site.

Every one of them goes through `resolve_owner` first, which is where the
permission is actually checked.

The secret never travels as an argument to a command and never lands in the
sandbox's filesystem where the agent could read it: it goes in the body of an
authenticated request to the relay, which hands it straight to the browser.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.web_login.domain.entities import (
    PendingSignIn,
    SignInOutcome,
    WebLoginSecret,
)
from app.modules.web_login.infrastructure.repository import WebLoginRepository
from app.modules.web_login.services.origin import normalize_origin
from app.modules.web_login.services.pauses import (
    ReadPause,
    ResumePause,
    pending_through_contracts,
    resume_through_approvals,
)
from app.modules.web_login.services.resolution import resolve_owner
from app.modules.web_login.services.scope import (
    BrowserState,
    host_of,
    looks_signed_in,
    scope_state,
)
from sandbox_runtime.errors import SandboxCapabilityUnsupported

if TYPE_CHECKING:
    from app.modules.workspace.contracts.browser import BrowserState

logger = get_logger(__name__)


#: Words a page shows when it still wants a login. Crude on purpose: the
#: alternative is asking a model, and a wrong answer here either asks a person
#: who did not need asking or reports a sign-in that did not happen.
_WALL_HINTS = ("sign in", "signin", "log in", "login", "password")


def _agent_browser(conversation_id: UUID | None) -> str | None:
    """The browser session a conversation's agent works in, if one is named.

    `None` leaves the relay to decide from the site, which is what a sign-in
    wants and what everything else got by accident.
    """
    if conversation_id is None:
        return None
    from app.modules.workspace.contracts.browser import agent_session

    return agent_session(conversation_id)


class SignInService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        browser: object | None = None,
        resume: "ResumePause | None" = None,
        read_pause: "ReadPause | None" = None,
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

    @property
    def _browser(self):
        """The browser, built on first use.

        Deferred because constructing it imports the whole workspace provider
        stack -- Docker, the E2B SDK, httpx -- and every process that merely
        registers these routes would pay for it at import. The sign-in paths
        that touch a browser are a minority of what this service does.
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

    async def try_saved_login(
        self,
        *,
        origin: str,
        conversation_id: UUID | None = None,
        auth_ctx: Context | None = None,
    ) -> tuple[bool, str]:
        """Load a stored session for this site into the browser that will use it.

        Returns whether the browser now holds one, and a sentence for the
        agent. A session that is present but marked dead is not tried: the
        point of marking it was to stop a run failing on it.

        **`conversation_id` names the browser the agent works in**, and getting
        that wrong was the whole feature failing quietly. A capture is taken
        from `login-<host>` -- a separate Chrome, so that what is captured is
        bounded by the site the person signed in to, and so that the agent
        cannot drive the page while somebody types a password into it. But the
        *load* has to land where the agent browses. It went to `login-<host>`
        too: the cookies were injected into a browser nothing else opened, the
        check that the site accepted them looked at that same browser and
        passed, and the agent carried on signed out with "signed in with a
        saved login" in its transcript.
        """
        owner = await resolve_owner(auth_ctx=auth_ctx)
        site = normalize_origin(origin)
        domain = host_of(site)

        async with self._uow_factory() as uow:
            repository = WebLoginRepository(uow.session)
            saved = await repository.get_for_origin(owner, site)
            if saved is None:
                return False, "no saved login for this site"
            if not saved.is_usable:
                return False, "the saved login for this site has stopped working"
            secret = await repository.reveal_secret(owner, site)

        if secret is None or secret.is_empty():
            return False, "the saved login for this site is empty"

        # Outside the unit of work: a sandbox round trip must not be made
        # holding a pooled database connection.
        try:
            await self._browser.load_login_state(
                owner,
                {"cookies": secret.cookies, "origins": secret.origins},
                domain=domain,
                session=_agent_browser(conversation_id),
            )
        except (_relay_unavailable(), SandboxCapabilityUnsupported) as exc:
            await self._audit(
                owner, site, action="inject", outcome="failed", detail=str(exc)
            )
            return False, "the saved login could not be loaded into the browser"

        # Loading a session is not the same as the site accepting it, and this
        # used to report success on the strength of the load alone. A revoked
        # or expired session then read as "signed in" for ever: the tool told
        # the agent to call again if the page still asked for a login, the next
        # call loaded the same dead state, and said "signed in" again. Nothing
        # in production ever marked a login dead -- the method for it existed
        # with no caller.
        if not await self._site_accepted(owner, site, conversation_id=conversation_id):
            await self.mark_saved_login_dead(origin=site, auth_ctx=auth_ctx)
            return False, "the saved login for this site has stopped working"

        async with self._uow_factory() as uow:
            await WebLoginRepository(uow.session).mark_used(owner, site)
        await self._audit(owner, site, action="inject", outcome="ok")
        return True, "signed in with a saved login"

    async def _site_accepted(
        self, owner: UUID, site: str, *, conversation_id: UUID | None = None
    ) -> bool:
        """Whether the site let us in, judged by where the browser ended up.

        Open the page with the session loaded and look at what came back. A
        site that rejected it sends the browser to a login form, and both the
        address and the page's own title say so.

        This reads the destination rather than the page body because the
        destination is already in the reply -- no second round trip into the
        sandbox for something that is true in the common case. It is not a
        complete test, and is not claimed to be: a site that serves a login
        form at the same address under a neutral title will pass it. What it
        removes is the failure that mattered, which was reporting success
        without looking at all. A run that gets past this and still meets a
        wall has `browser_sign_in` to fall back to.
        """
        try:
            landed = await self._browser.ensure_for_sign_in(
                owner,
                origin=site,
                report=True,
                session=_agent_browser(conversation_id),
            )
        except _relay_unavailable(), SandboxCapabilityUnsupported:
            # The browser is not reachable, which says nothing either way about
            # the session. Treated as accepted so an unreachable sandbox does
            # not mark a working login dead.
            return True
        if not isinstance(landed, dict):
            return True
        return not page_looks_like_a_login_wall(
            f"{landed.get('url', '')} {landed.get('title', '')}"
        )

    async def mark_saved_login_dead(
        self, *, origin: str, auth_ctx: Context | None = None
    ) -> None:
        """Record that a stored session no longer works.

        Called when a page loaded with an injected session still shows a login
        wall. This is what makes the next run ask the person instead of failing
        the same way again.
        """
        owner = await resolve_owner(auth_ctx=auth_ctx)
        site = normalize_origin(origin)
        async with self._uow_factory() as uow:
            await WebLoginRepository(uow.session).mark_dead(owner, site)
        await self._audit(
            owner, site, action="inject", outcome="failed", detail="session rejected"
        )

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
        del reason, tool_call_id  # carried by the pause, not by this call

        try:
            await self._browser.ensure_for_sign_in(owner, origin=site)
        except _relay_unavailable(), SandboxCapabilityUnsupported:
            # Not fatal: the arrival opens the browser again. Logged because a
            # person landing on a cold browser waits, and knowing it started
            # cold is what explains the wait.
            logger.warning("web_login.sign_in.browser_not_ready.degraded")

        await self._audit(
            owner,
            site,
            action="request",
            outcome="opened",
            conversation_id=conversation_id,
        )
        return site

    async def pending(
        self, *, conversation_id: UUID, user_id: UUID
    ) -> PendingSignIn | None:
        """What a sign-in link is for, read from the pause itself.

        There is no row to read. The paused tool call carries the origin and the
        reason the agent gave, and its still being unresolved is what "waiting"
        means -- so the three facts a sign-in page needs are the pause.
        """
        async with self._uow_factory() as uow:
            paused = await self._read_pause(uow, conversation_id)
        if paused is None:
            return None
        del user_id  # the caller has already matched the conversation's owner
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
        force: bool = False,
    ) -> SignInOutcome:
        """Capture what the person did, and let the waiting run carry on.

        One method for both answers because they are one answer: a person is
        telling us whether they signed in. It used to be `finish` and `decline`
        against a row with its own status, and the two drifted -- `decline`
        guarded against overwriting a resolved request and `finish` did not, so
        a stale tab could flip a declined sign-in to signed-in while the agent
        had already been told otherwise.

        Nothing guards that here because nothing can: the decision row is the
        lock, first writer wins, and this resolves through the same endpoint an
        approval button does.

        Refuses when the browser holds nothing for this site, unless forced --
        said while the person is still here and can do something about it,
        rather than stored as a login that will not work.
        """
        found = await self.pending(conversation_id=conversation_id, user_id=user_id)
        if found is None or found.tool_call_id != tool_call_id:
            raise SignInNotPending(tool_call_id)

        site = found.origin
        if not signed_in:
            await self._audit(user_id, site, action="request", outcome="declined")
            await self._tell_the_agent(
                conversation_id=conversation_id,
                tool_call_id=tool_call_id,
                user_id=user_id,
                approved=False,
            )
            return SignInOutcome(origin=site, signed_in=False, saved=False)

        saved = False
        detail: str | None = None
        domain = host_of(site)

        try:
            state: (
                BrowserState | dict[str, object]
            ) = await self._browser.save_login_state(user_id, domain=domain)
        except (_relay_unavailable(), SandboxCapabilityUnsupported) as exc:
            state = {}
            detail = f"the browser could not be read: {exc}"

        if state and not looks_signed_in(state, origin=site) and not force:
            raise NotSignedInYet(site)

        if state:
            scoped = scope_state(state, origin=site)
            if scoped["cookies"] or scoped["origins"]:
                async with self._uow_factory() as uow:
                    await WebLoginRepository(uow.session).save(
                        user_id=user_id,
                        origin=site,
                        secret=WebLoginSecret(
                            cookies=scoped["cookies"], origins=scoped["origins"]
                        ),
                    )
                saved = True
                # Handed to the browser the run will resume into, here and not
                # on the next run. The person signed in to `login-<host>`; the
                # agent works in the conversation's own browser, and without
                # this it resumes into one that has never seen the site. Saving
                # and transferring are two steps because they are two browsers,
                # and the whole point of the second one is that it is separate.
                await self._hand_to_the_agent(
                    user_id,
                    origin=site,
                    conversation_id=conversation_id,
                    scoped=scoped,
                )
            else:
                detail = detail or "nothing for this site was in the browser"

        await self._audit(
            user_id,
            site,
            action="capture",
            outcome="ok" if saved else "empty",
            detail=detail,
            conversation_id=conversation_id,
        )
        await self._tell_the_agent(
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            user_id=user_id,
            approved=True,
            saved=saved,
            saved_detail=detail,
        )
        return SignInOutcome(
            origin=site, signed_in=True, saved=saved, saved_detail=detail
        )

    async def _hand_to_the_agent(
        self,
        owner: UUID,
        *,
        origin: str,
        conversation_id: UUID | None,
        scoped: BrowserState,
    ) -> None:
        """Put the captured session into the browser the run resumes into.

        Best effort, and deliberately not fatal: the login is already stored, so
        a transfer that fails costs the run one more `browser_sign_in` -- which
        will find the saved login and load it -- rather than losing what the
        person just did.
        """
        session = _agent_browser(conversation_id)
        if session is None:
            return
        try:
            await self._browser.load_login_state(
                owner,
                {"cookies": scoped["cookies"], "origins": scoped["origins"]},
                domain=host_of(origin),
                session=session,
            )
        except (_relay_unavailable(), SandboxCapabilityUnsupported) as exc:
            await self._audit(
                owner,
                origin,
                action="inject",
                outcome="failed",
                detail=f"could not reach the agent's browser: {exc}",
                conversation_id=conversation_id,
            )

    async def _tell_the_agent(
        self,
        *,
        conversation_id: UUID | None,
        tool_call_id: str | None,
        user_id: UUID,
        approved: bool,
        saved: bool = False,
        saved_detail: str | None = None,
    ) -> None:
        """Resolve the paused tool call, carrying the outcome with it.

        `tool_call_id` is the approval id, so this goes through the same
        endpoint an approval button does -- idempotent, because the decision row
        is the double-submit lock, and self-healing, which is what makes it safe
        to call from a retry.

        `saved` and `saved_detail` ride on the decision's `response`, which is
        the channel `ask_user` already uses for its answers. They used to live in
        a table of this feature's own, which the resume path then had to go and
        read; two stores for two booleans, and they disagreed.

        `approved` maps to APPROVE_ONCE, never APPROVE_FOR_SESSION: signing in
        once is not standing consent to be asked nothing next time. What makes
        the next run quiet is the saved login, which the person can see and
        delete -- not a blanket approval they never gave.
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
                response={"saved": saved, "saved_detail": saved_detail},
            )
        if not reached:
            # The conversation is gone. The person still finished, and their
            # login is still saved -- so this is worth a line, not an error
            # thrown back at somebody who did what was asked of them.
            logger.warning(
                "web_login.sign_in.conversation_gone.degraded",
                conversation_id=str(conversation_id),
            )

    async def _audit(
        self,
        user_id: UUID,
        origin: str,
        *,
        action: str,
        outcome: str,
        detail: str | None = None,
        conversation_id: UUID | None = None,
    ) -> None:
        """Append to the trail a person can read back.

        `conversation_id` is passed because it is known here and the column
        existed unwritten: a credential log that cannot say which run used a
        login answers half the question it is for.
        """
        async with self._uow_factory() as uow:
            await WebLoginRepository(uow.session).record(
                user_id=user_id,
                origin=origin,
                action=action,
                outcome=outcome,
                detail=detail,
                conversation_id=conversation_id,
            )


def _relay_unavailable() -> type[Exception]:
    """The relay's own failure type, imported when it is needed.

    A function rather than a module-level import for the same reason the
    browser is: naming it at import time pulls the provider stack in.
    """
    from app.modules.workspace.contracts.browser import browser_unavailable

    return browser_unavailable()


class NotSignedInYet(Exception):
    """The browser holds nothing for this site, so there is nothing to keep."""


class SignInNotPending(Exception):
    """Nothing is waiting on this answer.

    Either the run was never paused for this site, or somebody already answered.
    Both are the same fact from the page's side -- the link has been used -- and
    both used to be a row lookup returning a resolved status.
    """

    def __init__(self, tool_call_id: str) -> None:
        super().__init__(f"no sign-in is waiting on {tool_call_id}")
        self.tool_call_id = tool_call_id


def page_looks_like_a_login_wall(text: str) -> bool:
    """Whether a page still appears to want a login.

    Used after loading a saved session: if the site shows a login form anyway,
    the session is dead and saying so now is what `PS-CONN-022` asks for.
    """
    lowered = (text or "").lower()[:4000]
    return any(hint in lowered for hint in _WALL_HINTS)


__all__ = ["NotSignedInYet", "SignInService", "page_looks_like_a_login_wall"]
