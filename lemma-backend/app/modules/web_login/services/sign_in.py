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
    SignInRequest,
    SignInRequestStatus,
    WebLoginSecret,
)
from app.modules.web_login.infrastructure.repository import WebLoginRepository
from app.modules.web_login.infrastructure.sign_in_repository import (
    SignInRequestRepository,
)
from app.modules.web_login.services.origin import normalize_origin
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


class SignInService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        browser: object | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._browser_override = browser
        self._browser_built: object | None = None

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
        self, *, origin: str, auth_ctx: Context | None = None
    ) -> tuple[bool, str]:
        """Load a stored session for this site, if there is a usable one.

        Returns whether the browser now holds one, and a sentence for the
        agent. A session that is present but marked dead is not tried: the
        point of marking it was to stop a run failing on it.
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
            )
        except (_relay_unavailable(), SandboxCapabilityUnsupported) as exc:
            await self._audit(
                owner, site, action="inject", outcome="failed", detail=str(exc)
            )
            return False, "the saved login could not be loaded into the browser"

        async with self._uow_factory() as uow:
            await WebLoginRepository(uow.session).mark_used(owner, site)
        await self._audit(owner, site, action="inject", outcome="ok")
        return True, "signed in with a saved login"

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
    ) -> SignInRequest:
        """Record that a person is being asked, and put the site in front of them.

        The browser is opened here rather than when they arrive so that the
        common case -- somebody who clicks straight away -- finds the site
        already loaded. It is best effort: by the time a person opens a link
        sent to their phone, the browser may well have retired for idleness,
        and the arrival re-opens it.
        """
        owner = await resolve_owner(auth_ctx=auth_ctx)
        site = normalize_origin(origin)

        async with self._uow_factory() as uow:
            request = await SignInRequestRepository(uow.session).create(
                user_id=owner,
                origin=site,
                reason=reason,
                conversation_id=conversation_id,
                tool_call_id=tool_call_id,
            )

        try:
            await self._browser.ensure_for_sign_in(owner, origin=site)
        except _relay_unavailable(), SandboxCapabilityUnsupported:
            # Not fatal: the arrival opens the browser again. Logged because a
            # person landing on a cold browser waits, and knowing it started
            # cold is what explains the wait.
            logger.warning("web_login.sign_in.browser_not_ready.degraded")

        await self._audit(owner, site, action="request", outcome="opened")
        return request

    async def finish(
        self,
        *,
        request_id: UUID,
        user_id: UUID,
        force: bool = False,
    ) -> SignInRequest:
        """Capture what the person signed in to, and close the request.

        Refuses when the browser holds nothing for this site, unless forced.
        Saving an empty capture would mean telling somebody their login was
        kept and then asking them again on the very next run.
        """
        async with self._uow_factory() as uow:
            repository = SignInRequestRepository(uow.session)
            request = await repository.get_for_user(request_id, user_id)
        if request is None:
            from app.modules.web_login.infrastructure.sign_in_repository import (
                SignInRequestNotFound,
            )

            raise SignInRequestNotFound(str(request_id))

        site = request.origin
        domain = host_of(site)
        saved = False
        detail: str | None = None

        try:
            state: (
                BrowserState | dict[str, object]
            ) = await self._browser.save_login_state(user_id, domain=domain)
        except (_relay_unavailable(), SandboxCapabilityUnsupported) as exc:
            state = {}
            detail = f"the browser could not be read: {exc}"

        if state and not looks_signed_in(state, origin=site) and not force:
            # Said as a refusal rather than stored, while they are still here
            # and can do something about it.
            raise NotSignedInYet(site)

        if state:
            scoped = scope_state(state, origin=site)
            if scoped["cookies"] or scoped["origins"]:
                async with self._uow_factory() as uow:
                    await WebLoginRepository(uow.session).save(
                        user_id=user_id,
                        origin=site,
                        label=domain,
                        secret=WebLoginSecret(
                            cookies=scoped["cookies"], origins=scoped["origins"]
                        ),
                    )
                saved = True
            else:
                detail = detail or "nothing for this site was in the browser"

        async with self._uow_factory() as uow:
            resolved = await SignInRequestRepository(uow.session).resolve(
                request_id,
                user_id,
                status=SignInRequestStatus.SIGNED_IN,
                saved=saved,
                saved_detail=detail,
            )
        await self._audit(
            user_id,
            site,
            action="capture",
            outcome="ok" if saved else "empty",
            detail=detail,
        )
        return resolved

    async def decline(self, *, request_id: UUID, user_id: UUID) -> SignInRequest:
        async with self._uow_factory() as uow:
            resolved = await SignInRequestRepository(uow.session).resolve(
                request_id, user_id, status=SignInRequestStatus.DECLINED
            )
        await self._audit(
            user_id, resolved.origin, action="request", outcome="declined"
        )
        return resolved

    async def _audit(
        self,
        user_id: UUID,
        origin: str,
        *,
        action: str,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            await WebLoginRepository(uow.session).record(
                user_id=user_id,
                origin=origin,
                action=action,
                outcome=outcome,
                detail=detail,
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


def page_looks_like_a_login_wall(text: str) -> bool:
    """Whether a page still appears to want a login.

    Used after loading a saved session: if the site shows a login form anyway,
    the session is dead and saying so now is what `PS-CONN-022` asks for.
    """
    lowered = (text or "").lower()[:4000]
    return any(hint in lowered for hint in _WALL_HINTS)


__all__ = ["NotSignedInYet", "SignInService", "page_looks_like_a_login_wall"]
