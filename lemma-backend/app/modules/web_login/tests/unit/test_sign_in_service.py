"""Getting a run past a login wall.

Much smaller than it was, and the reason is the point of the change. This file
used to fake a database session so the real repository ran -- encrypting a
capture, scoping it to a site, mapping it back to an entity -- because the
service's core decision was "which stored cookies are this person's login for
this site, and do they still work". Every one of those steps was a place to be
wrong, and three of them were.

There is nothing stored now. The browser keeps its own profile, so the
decisions left are: is the site already letting us in, does the person need
asking, and did their answer reach the run that was waiting. Those are the
tests below.

The doubles sit in front of the subject rather than inside it -- the browser,
the pause reader, the resume -- which is what the doubles gate asks for and
what makes a rename of a real collaborator fail here instead of slipping past.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.web_login.services.sign_in import (
    SignInNotPending,
    SignInService,
    page_looks_like_a_login_wall,
)

SITE = "https://app.example.com"
#: Whose conversation it is. Named rather than a fresh `uuid4()` per call site
#: because the service compares it against the caller, and two anonymous uuids
#: that happen not to match is the bug this exists to catch.
OWNER = uuid4()

pytestmark = pytest.mark.unit


class _Ctx:
    def __init__(self, user_id) -> None:
        self.user_id = user_id
        self.delegated_by_user_id = None
        self.is_user_equivalent = False
        self.pod_id = None

    async def require(self, *_args, **_kwargs) -> None:
        return None


class _Browser:
    """The sandbox browser, or a refusal from it."""

    def __init__(
        self, *, fails: Exception | None = None, landed: dict | None = None
    ) -> None:
        self.fails = fails
        #: Where opening the site left the browser. The default is the site
        #: itself under a neutral title, i.e. "it let us in".
        self.landed = landed or {"url": "https://app.example.com/home", "title": "Home"}
        self.opened: list[str] = []
        self.closed = False

    async def ensure_for_sign_in(self, _user_id, *, origin, report: bool = False):
        if self.fails:
            raise self.fails
        self.opened.append(origin)
        return self.landed if report else None

    async def close(self):
        self.closed = True


#: A wall: the address says `/login` and the title says so too.
AT_A_LOGIN_FORM = {"url": "https://app.example.com/login", "title": "Sign in"}

#: `_service(owner=...)` left alone means "the caller owns it". A sentinel
#: rather than `None`, because `None` is itself an answer the real lookup
#: gives -- a conversation that no longer exists -- and the two must not
#: collapse into each other.
_ITS_THEIRS = object()

GONE = None


class _Owner:
    def __init__(self, owner: object = _ITS_THEIRS) -> None:
        self.owner = OWNER if owner is _ITS_THEIRS else owner

    async def __call__(self, _uow, _conversation_id):
        return self.owner


class _Waiting:
    """The conversation's unresolved sign-in, as the pause reports it.

    This *is* the sign-in request. It used to be a row carrying an origin, a
    reason and a status; the first two are the paused call's arguments and the
    third is whether this returns anything at all.
    """

    def __init__(
        self, *, tool_call_id="call-1", origin=SITE, reason="pulling invoices"
    ):
        self.pause = SimpleNamespace(
            tool_call_id=tool_call_id,
            kind="browser_sign_in",
            tool_args={"origin": origin, "reason": reason},
            agent_run_id=None,
        )
        self.calls: list[UUID] = []

    async def __call__(self, _uow, conversation_id):
        self.calls.append(conversation_id)
        return self.pause


class _NothingWaiting:
    async def __call__(self, _uow, _conversation_id):
        return None


class _Resume:
    """How the service closes the pause a sign-in was raised for."""

    def __init__(self, *, reached: bool = True) -> None:
        self.reached = reached
        self.calls: list[dict] = []

    async def __call__(self, _uow, **kwargs) -> bool:
        self.calls.append(kwargs)
        return self.reached


def _service(
    browser: _Browser,
    resume: "_Resume | None" = None,
    waiting: object | None = None,
    owner: object = _ITS_THEIRS,
) -> SignInService:
    def factory():
        class _CM:
            async def __aenter__(self):
                return SimpleNamespace(session=None)

            async def __aexit__(self, *exc):
                return False

        return _CM()

    return SignInService(
        factory,
        browser=browser,
        resume=resume or _Resume(),
        read_pause=waiting if waiting is not None else _Waiting(),
        owner_of=_Owner(owner),
    )


def _relay_error() -> type[Exception]:
    from app.modules.workspace.contracts.browser import browser_unavailable

    return browser_unavailable()


# ---------------------------------------------------------------------------
# Is the browser already signed in?
# ---------------------------------------------------------------------------


async def test_a_site_that_does_not_ask_for_a_login_needs_no_person() -> None:
    browser = _Browser()
    assert await _service(browser).already_signed_in(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )
    assert browser.opened == [SITE]


async def test_a_site_that_shows_a_login_form_needs_the_person() -> None:
    """The check reads where the browser *landed*, not what cookies exist.

    Its predecessor asked the cookies, and a consent banner's flag answered
    yes -- which is how a person came to be told their login had been kept
    when nothing had been.
    """
    browser = _Browser(landed=AT_A_LOGIN_FORM)
    assert not await _service(browser).already_signed_in(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )


async def test_an_unreachable_browser_is_not_read_as_signed_in() -> None:
    """Not knowing is a reason to ask, never a reason to claim."""
    browser = _Browser(fails=_relay_error()("no relay"))
    assert not await _service(browser).already_signed_in(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )


async def test_a_browser_that_reports_nothing_is_not_read_as_signed_in() -> None:
    browser = _Browser(landed={})
    browser.landed = None  # type: ignore[assignment]
    assert not await _service(browser).already_signed_in(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------


async def test_asking_puts_the_site_in_front_of_the_person() -> None:
    browser = _Browser()
    site = await _service(browser).open_request(
        origin="app.example.com",
        reason="pulling invoices",
        conversation_id=uuid4(),
        tool_call_id="call-1",
        auth_ctx=_Ctx(uuid4()),
    )
    assert site == SITE, "a bare host is normalised to an https origin"
    assert browser.opened == [SITE]


async def test_a_cold_browser_does_not_fail_the_ask() -> None:
    """Best effort on purpose: the arrival opens the browser again, and by
    the time somebody reads a link on their phone it has often retired."""
    browser = _Browser(fails=_relay_error()("no relay"))
    site = await _service(browser).open_request(
        origin=SITE,
        reason="pulling invoices",
        conversation_id=uuid4(),
        tool_call_id="call-1",
        auth_ctx=_Ctx(uuid4()),
    )
    assert site == SITE


# ---------------------------------------------------------------------------
# Reading and answering the pause
# ---------------------------------------------------------------------------


async def test_somebody_elses_conversation_reads_as_nothing_waiting() -> None:
    """Indistinguishable from "already answered" on purpose: saying which
    would tell a stranger the conversation exists."""
    found = await _service(_Browser(), owner=uuid4()).pending(
        conversation_id=uuid4(), user_id=OWNER
    )
    assert found is None


async def test_a_conversation_that_is_gone_reads_as_nothing_waiting() -> None:
    found = await _service(_Browser(), owner=GONE).pending(
        conversation_id=uuid4(), user_id=OWNER
    )
    assert found is None


async def test_answering_a_link_nobody_is_waiting_on_is_refused() -> None:
    with pytest.raises(SignInNotPending):
        await _service(_Browser(), waiting=_NothingWaiting()).answer(
            conversation_id=uuid4(),
            tool_call_id="call-1",
            user_id=OWNER,
            signed_in=True,
        )


async def test_signing_in_resumes_the_run_and_reports_the_site_is_happy() -> None:
    resume = _Resume()
    outcome = await _service(_Browser(), resume).answer(
        conversation_id=uuid4(),
        tool_call_id="call-1",
        user_id=OWNER,
        signed_in=True,
    )
    assert (outcome.signed_in, outcome.working) == (True, True)
    assert resume.calls[0]["approved"] is True
    assert resume.calls[0]["response"] == {"working": True}


async def test_a_site_still_showing_a_form_is_reported_not_refused() -> None:
    """The person has already done what was asked. Telling the agent "they
    say they signed in but the page still shows a form" is more use than
    refusing them -- and the check is a heuristic, so refusing on it would
    strand anyone whose site it reads wrongly.
    """
    resume = _Resume()
    outcome = await _service(_Browser(landed=AT_A_LOGIN_FORM), resume).answer(
        conversation_id=uuid4(),
        tool_call_id="call-1",
        user_id=OWNER,
        signed_in=True,
    )
    assert outcome.signed_in is True
    assert outcome.working is False
    assert resume.calls[0]["approved"] is True


async def test_declining_resumes_the_run_without_touching_the_browser() -> None:
    browser = _Browser()
    resume = _Resume()
    outcome = await _service(browser, resume).answer(
        conversation_id=uuid4(),
        tool_call_id="call-1",
        user_id=OWNER,
        signed_in=False,
    )
    assert (outcome.signed_in, outcome.working) == (False, False)
    assert resume.calls[0]["approved"] is False
    assert browser.opened == [], "there is nothing to check if they did not sign in"


async def test_a_conversation_that_vanished_does_not_fail_the_person() -> None:
    """They did what was asked and the browser is signed in. That is a log
    line, not an error thrown back at them."""
    outcome = await _service(_Browser(), _Resume(reached=False)).answer(
        conversation_id=uuid4(),
        tool_call_id="call-1",
        user_id=OWNER,
        signed_in=True,
    )
    assert outcome.signed_in is True


# ---------------------------------------------------------------------------
# The wall heuristic itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["https://x.test/login Home", "https://x.test/ Sign in", "https://x.test/ Password"],
)
def test_a_page_asking_for_a_login_is_recognised(text: str) -> None:
    assert page_looks_like_a_login_wall(text)


def test_an_ordinary_page_is_not_a_wall() -> None:
    assert not page_looks_like_a_login_wall("https://x.test/dashboard Dashboard")


def test_nothing_at_all_is_not_a_wall() -> None:
    assert not page_looks_like_a_login_wall("")


# ---------------------------------------------------------------------------
# What is no longer here
# ---------------------------------------------------------------------------


def test_the_service_cannot_store_or_restore_a_session() -> None:
    """Structural, because the guarantee is structural.

    A capture the backend holds is a capture the backend can get wrong, and
    did. There is no repository, no secret and no injection left to reach --
    which is a stronger statement than any test of their behaviour.
    """
    from app.modules.web_login.services import sign_in as module

    for gone in (
        "capture",
        "try_saved_login",
        "mark_saved_login_dead",
        "_hand_to_the_agent",
        "_site_accepted",
    ):
        assert not hasattr(SignInService, gone), f"{gone} should be gone"
    assert "WebLoginRepository" not in dir(module)
