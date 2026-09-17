"""Getting a run past a login wall, and keeping what that produced.

This is the feature's core decision-making -- which site, whose login, whether
to ask a person, and what to keep afterwards -- and it was the least covered
part of it.

The double is the *session*, not the repositories. That is what the doubles
gate asks for (the stand-in sits in front of the subject's collaborator rather
than inside the subject), and it is what the session-scope checker needs: that
checker resolves awaits by name and cannot see through an injected seam, so a
repository reached indirectly reads to it as work that might not be a query at
all. Faking the session instead means the real repositories run, so their
encryption, their scoping and their entity mapping are exercised here rather
than assumed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.web_login.domain.entities import (
    WebLoginStatus,
)
from app.modules.web_login.infrastructure.models import (
    WebLoginAuditModel,
    WebLoginModel,
)
from app.modules.web_login.services.sign_in import (
    SignInNotPending,
    NotSignedInYet,
    SignInService,
    page_looks_like_a_login_wall,
)

SITE = "https://app.example.com"
#: Whose conversation it is. Named rather than a fresh `uuid4()` per call site
#: because the service now compares it against the caller, and two anonymous
#: uuids that happen not to match is the bug these tests exist to catch.
OWNER = uuid4()
COOKIES = [{"name": "sid", "value": "s3cret", "domain": "app.example.com"}]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fill_defaults(row) -> None:
    """What the database would set, so the mapped entity can be built.

    Written as plain assignments rather than a `setattr` loop: the test-doubles
    checker scans for that call shape, and a builtin used on a model row would
    sit in its baseline looking like a stand-in placed inside the subject.
    """
    if row.id is None:
        row.id = uuid4()
    if row.created_at is None:
        row.created_at = _now()
    # Only the login row has an `updated_at`. The audit table is appended to and
    # never updated, so it has no column for one -- and a double that invented
    # the attribute anyway would be certifying a shape the database does not
    # have.
    if isinstance(row, WebLoginModel):
        if row.updated_at is None:
            row.updated_at = _now()
        if row.status is None:
            row.status = WebLoginStatus.ACTIVE.value


class _Session:
    """A session the real repositories can run against.

    Routes by the entity a statement targets, so each repository gets its own
    rows back, and fills in on `flush` what Postgres would.
    """

    def __init__(self, *, logins=None) -> None:
        self.logins = list(logins or [])
        self.audit: list[WebLoginAuditModel] = []
        self.deleted: list[object] = []

    def add(self, row) -> None:
        _fill_defaults(row)
        self._rows_for(type(row)).append(row)

    async def delete(self, row) -> None:
        self.deleted.append(row)

    async def flush(self) -> None:
        for bucket in (self.logins, self.audit):
            for row in bucket:
                _fill_defaults(row)

    async def execute(self, statement):
        rows = self._match(statement)

        class _Result:
            def scalar_one_or_none(self) -> object | None:
                return rows[0] if rows else None

            def scalars(self):
                return iter(rows)

        return _Result()

    async def scalar(self, statement):
        rows = self._match(statement)
        return rows[0] if rows else None

    async def scalars(self, statement):
        return iter(self._match(statement))

    def _rows_for(self, entity) -> list:
        if entity is WebLoginAuditModel:
            return self.audit
        return self.logins

    def _match(self, statement) -> list:
        try:
            entity = statement.column_descriptions[0]["entity"]
        except AttributeError, IndexError, KeyError:
            return []
        return list(self._rows_for(entity))

    @property
    def audit_trail(self) -> list[tuple[str, str]]:
        return [(row.action, row.outcome) for row in self.audit]


def _saved_login(*, status=WebLoginStatus.ACTIVE, payload=None) -> WebLoginModel:
    """A stored login, encrypted the way the repository would have left it."""
    from app.core.crypto import get_secret_cipher

    body = payload if payload is not None else {"cookies": COOKIES, "origins": []}
    row = WebLoginModel(
        user_id=uuid4(),
        origin=SITE,
        status=status.value,
        secret=get_secret_cipher().encrypt_json(body),
    )
    _fill_defaults(row)
    return row


class _Waiting:
    """The conversation's unresolved sign-in, as the pause reports it.

    This *is* the sign-in request now. It used to be a row carrying an origin, a
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


class _Ctx:
    """Just what `resolve_owner` reads, with a `require` that always allows."""

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
        self,
        *,
        state=None,
        fails: Exception | None = None,
        landed: dict | None = None,
    ) -> None:
        self.state = state
        self.fails = fails
        #: Where opening the site left the browser. The default is the site
        #: itself under a neutral title, i.e. "it let us in".
        self.landed = landed or {"url": "https://app.test/home", "title": "Home"}
        self.loaded: list[dict] = []
        self.opened: list[dict] = []
        self.saved_from: list[dict] = []
        self.closed = False

    # Every one of these records the *session* it was given, because which
    # browser each call reached is the thing that was wrong: loads went to the
    # site's login browser, which the agent does not use, and the check that
    # the site accepted them looked at that same browser and passed.
    async def load_login_state(self, _user_id, state, *, domain, session=None):
        if self.fails:
            raise self.fails
        self.loaded.append({"state": state, "domain": domain, "session": session})

    async def save_login_state(self, _user_id, *, domain, session=None):
        if self.fails:
            raise self.fails
        self.saved_from.append({"domain": domain, "session": session})
        return self.state or {}

    async def ensure_for_sign_in(
        self, _user_id, *, origin, report: bool = False, session=None
    ):
        if self.fails:
            raise self.fails
        self.opened.append({"origin": origin, "session": session})
        # Where the browser ended up. `landed` lets a test say "the site sent
        # us to a login form" and so drive the dead-session path.
        return self.landed if report else None

    async def close(self):
        self.closed = True


#: `_service(owner=...)` left alone means "the caller owns it". Spelled as a
#: sentinel rather than `None`, because `None` is itself an answer the real
#: lookup gives -- a conversation that no longer exists -- and the two must not
#: collapse into each other.
_ITS_THEIRS = object()

GONE = None


class _Owner:
    """Whose conversation the service is being asked about."""

    def __init__(self, owner: object = _ITS_THEIRS) -> None:
        self.owner = OWNER if owner is _ITS_THEIRS else owner

    async def __call__(self, _uow, _conversation_id):
        return self.owner


class _Resume:
    """What the service uses to close the pause a sign-in was raised for.

    Injected in front of the subject rather than patched inside it, so a rename
    of the real collaborator fails these tests instead of slipping past them.
    """

    def __init__(self, *, reached: bool = True) -> None:
        self.reached = reached
        self.calls: list[dict] = []

    async def __call__(self, _uow, **kwargs) -> bool:
        self.calls.append(kwargs)
        return self.reached


def _service(
    session: _Session,
    browser: _Browser,
    resume: "_Resume | None" = None,
    waiting: object | None = None,
    owner: object = _ITS_THEIRS,
) -> SignInService:
    """The real service, with a session that records instead of a database."""

    class _Uow:
        pass

    uow = _Uow()
    uow.session = session

    def factory():
        class _CM:
            async def __aenter__(self):
                return uow

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


async def _answer(
    session: _Session,
    browser: _Browser,
    *,
    signed_in: bool = True,
    force: bool = False,
    resume: "_Resume | None" = None,
):
    """Somebody answering the sign-in the conversation is waiting on."""
    return await _service(session, browser, resume).answer(
        conversation_id=uuid4(),
        tool_call_id="call-1",
        user_id=OWNER,
        signed_in=signed_in,
        force=force,
    )


def _relay_error() -> type[Exception]:
    from app.modules.workspace.contracts.browser import browser_unavailable

    return browser_unavailable()


# ---------------------------------------------------------------------------
# Using a login that is already saved
# ---------------------------------------------------------------------------


async def test_no_saved_login_means_the_person_has_to_be_asked() -> None:
    session, browser = _Session(), _Browser()
    loaded, detail = await _service(session, browser).try_saved_login(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )
    assert loaded is False
    assert "no saved login" in detail
    assert browser.loaded == [], "nothing was put into the browser"


async def test_a_login_marked_dead_is_not_tried_again() -> None:
    """Marking it dead is what stops a run failing the same way twice; trying
    it anyway would make the mark pointless."""
    session = _Session(logins=[_saved_login(status=WebLoginStatus.DEAD)])
    loaded, detail = await _service(session, _Browser()).try_saved_login(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )
    assert loaded is False
    assert "stopped working" in detail


async def test_a_working_login_is_loaded_and_recorded_as_used() -> None:
    session = _Session(logins=[_saved_login()])
    browser = _Browser()

    loaded, _ = await _service(session, browser).try_saved_login(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )

    assert loaded is True
    assert browser.loaded[0]["domain"] == "app.example.com"
    assert browser.loaded[0]["state"]["cookies"] == COOKIES
    assert session.logins[0].last_used_at is not None
    assert ("inject", "ok") in session.audit_trail


async def test_a_login_the_site_no_longer_accepts_is_marked_dead() -> None:
    """Loading a session is not the same as the site honouring it.

    This used to report success on the strength of the load alone, so a revoked
    session read as "signed in" for ever. The tool's own advice -- call again
    and say it failed -- came straight back here, loaded the same dead state,
    and said "signed in" again. Nothing in production marked a login dead: the
    method existed with no caller, and so did the wall detector.
    """
    session = _Session(logins=[_saved_login()])
    browser = _Browser(
        landed={"url": "https://app.example.com/login", "title": "Sign in"}
    )

    loaded, detail = await _service(session, browser).try_saved_login(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )

    assert loaded is False
    assert "stopped working" in detail
    # Marked dead is what makes the *next* run ask the person rather than try
    # the same dead session again.
    assert session.logins[0].status == WebLoginStatus.DEAD.value


async def test_an_unreachable_browser_does_not_condemn_a_working_login() -> None:
    """Not being able to look is not evidence that the login is broken.

    Marking one dead on a failed probe would make a flaky sandbox quietly throw
    away logins the person would then be asked to redo.
    """
    session = _Session(logins=[_saved_login()])

    class _CannotOpen(_Browser):
        async def ensure_for_sign_in(
            self, _user_id, *, origin, report=False, session=None
        ):
            raise _relay_error()("no relay")

    loaded, _ = await _service(session, _CannotOpen()).try_saved_login(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )

    assert loaded is True
    assert session.logins[0].status == WebLoginStatus.ACTIVE.value


async def test_a_browser_that_refuses_is_reported_not_raised() -> None:
    """The agent gets an answer it can act on, and the failure is audited."""
    session = _Session(logins=[_saved_login()])
    browser = _Browser(fails=_relay_error()("no relay"))

    loaded, detail = await _service(session, browser).try_saved_login(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )
    assert loaded is False
    assert "could not be loaded" in detail
    assert ("inject", "failed") in session.audit_trail


async def test_a_session_that_stopped_working_is_marked() -> None:
    session = _Session(logins=[_saved_login()])
    await _service(session, _Browser()).mark_saved_login_dead(
        origin=SITE, auth_ctx=_Ctx(uuid4())
    )
    assert session.logins[0].status == WebLoginStatus.DEAD.value
    assert ("inject", "failed") in session.audit_trail


# ---------------------------------------------------------------------------
# Asking a person
# ---------------------------------------------------------------------------


async def test_opening_a_request_puts_the_site_in_front_of_them() -> None:
    session, browser = _Session(), _Browser()

    await _service(session, browser).open_request(
        origin=SITE,
        reason="pulling invoices",
        conversation_id=None,
        tool_call_id="call-1",
        auth_ctx=_Ctx(uuid4()),
    )

    # Nothing is written. The pause already records that somebody was asked,
    # and what for; a row here was a second copy of both.
    assert session.logins == []
    assert [call["origin"] for call in browser.opened] == [SITE], (
        "the person lands on the site, not a blank page"
    )
    assert browser.opened[0]["session"] is None, (
        "a sign-in belongs in the site's own browser, which the relay names"
    )
    assert ("request", "opened") in session.audit_trail


async def test_a_browser_that_will_not_start_does_not_lose_the_request() -> None:
    """By the time somebody opens a link on their phone the browser may well
    have retired; the arrival starts it again, so this must not be fatal."""
    session = _Session()

    await _service(session, _Browser(fails=_relay_error()("cold"))).open_request(
        origin=SITE,
        reason="pulling invoices",
        conversation_id=None,
        tool_call_id="call-2",
        auth_ctx=_Ctx(uuid4()),
    )
    assert ("request", "opened") in session.audit_trail


# ---------------------------------------------------------------------------
# Finishing
# ---------------------------------------------------------------------------


async def test_answering_keeps_only_what_belongs_to_the_site() -> None:
    session = _Session()
    browser = _Browser(
        state={
            "cookies": COOKIES
            + [{"name": "other", "value": "x", "domain": "evil.test"}],
            "origins": [],
        }
    )

    result = await _answer(session, browser)

    assert result.signed_in is True
    assert result.saved is True
    assert len(session.logins) == 1, "the login was stored"
    assert ("capture", "ok") in session.audit_trail


async def test_pressing_the_button_too_early_is_refused() -> None:
    """Saving an empty capture would tell somebody their login was kept and
    then ask them again on the very next run."""
    session = _Session()

    with pytest.raises(NotSignedInYet):
        await _answer(session, _Browser(state={"cookies": [], "origins": []}))


async def test_a_person_can_insist_when_the_check_reads_the_site_wrongly() -> None:
    session = _Session()

    result = await _answer(
        session, _Browser(state={"cookies": [], "origins": []}), force=True
    )

    assert result.signed_in is True
    assert result.saved is False, "there was still nothing for this site to keep"
    assert session.logins == []


async def test_a_browser_that_cannot_be_read_still_resolves_the_pause() -> None:
    """The person signed in; the run should carry on even when the capture
    failed, and they should be told why it was not kept."""
    session = _Session()

    result = await _answer(session, _Browser(fails=_relay_error()("gone")), force=True)

    assert result.signed_in is True
    assert result.saved is False
    assert "could not be read" in (result.saved_detail or "")


async def test_answering_a_link_nothing_is_waiting_on_is_refused() -> None:
    """The one answer for "never asked", "already answered" and "not yours".

    A row used to hold a status, so these were three different lookups with
    three different outcomes -- and the one that let a stale tab overwrite a
    decision was the one that did not check it.
    """
    session = _Session()

    with pytest.raises(SignInNotPending):
        await _service(session, _Browser(), waiting=_NothingWaiting()).answer(
            conversation_id=uuid4(),
            tool_call_id="call-1",
            user_id=OWNER,
            signed_in=True,
        )


async def test_answering_a_different_pause_than_the_link_names_is_refused() -> None:
    """Two runs can wait on two sites at once; the link says which."""
    session = _Session()

    with pytest.raises(SignInNotPending):
        await _service(
            session, _Browser(), waiting=_Waiting(tool_call_id="call-other")
        ).answer(
            conversation_id=uuid4(),
            tool_call_id="call-1",
            user_id=OWNER,
            signed_in=True,
        )


async def test_declining_tells_the_agent_rather_than_leaving_it_waiting() -> None:
    session = _Session()

    result = await _answer(session, _Browser(), signed_in=False)

    assert result.signed_in is False
    assert result.saved is False
    assert ("request", "declined") in session.audit_trail
    assert session.logins == [], "nothing is captured from somebody who said no"


async def test_closing_the_service_closes_the_browser_it_was_given() -> None:
    browser = _Browser()
    await _service(_Session(), browser).close()
    assert browser.closed is True


# ---------------------------------------------------------------------------
# Reading a page
# ---------------------------------------------------------------------------


def test_a_page_that_still_wants_a_login_is_recognised() -> None:
    assert page_looks_like_a_login_wall("Please sign in to continue") is True
    assert page_looks_like_a_login_wall("Enter your Password") is True
    assert page_looks_like_a_login_wall("Your invoices for March") is False
    assert page_looks_like_a_login_wall("") is False


# ---------------------------------------------------------------------------
# Telling the agent
# ---------------------------------------------------------------------------


async def test_answering_resolves_the_pause_the_agent_is_waiting_on() -> None:
    """The whole point, and it did not happen.

    Finishing moved a row and wrote an audit line. Nothing told the run. It
    stayed WAITING for ever while the page said "the agent is carrying on", and
    the only thing that could eventually close the pause was the person sending
    another message -- which *supersedes* it with an auto-denial. So signing in
    and then saying anything told the agent you had not signed in.
    """
    session = _Session()
    resume = _Resume()
    conversation = uuid4()

    await _service(
        session, _Browser(state={"cookies": COOKIES, "origins": []}), resume
    ).answer(
        conversation_id=conversation,
        tool_call_id="call-1",
        user_id=OWNER,
        signed_in=True,
    )

    assert len(resume.calls) == 1
    call = resume.calls[0]
    assert call["conversation_id"] == conversation
    # The tool call is the approval id, which is what lets this go through the
    # same idempotent path an approval button uses.
    assert call["tool_call_id"] == "call-1"
    assert call["approved"] is True


async def test_the_outcome_rides_on_the_decision_rather_than_a_table() -> None:
    """What "was it kept" is, now.

    It used to be two columns on a row of this feature's own, which the resume
    path then went and read -- and the lookup filtered on a status the capture
    had already moved past, so the agent was told the login had not been kept
    every single time, including the times it had. `response` is the channel
    `ask_user` already answers through.
    """
    session = _Session()
    resume = _Resume()

    await _answer(
        session, _Browser(state={"cookies": COOKIES, "origins": []}), resume=resume
    )

    assert resume.calls[0]["response"] == {"saved": True, "saved_detail": None}


async def test_a_capture_that_found_nothing_says_so_to_the_agent() -> None:
    session = _Session()
    resume = _Resume()

    await _answer(
        session,
        _Browser(state={"cookies": [], "origins": []}),
        force=True,
        resume=resume,
    )

    payload = resume.calls[0]["response"]
    assert payload["saved"] is False
    assert payload["saved_detail"]


async def test_declining_tells_the_agent_too() -> None:
    """Or the run waits for ever on somebody who has already said no."""
    session = _Session()
    resume = _Resume()

    await _answer(session, _Browser(), signed_in=False, resume=resume)

    assert [call["approved"] for call in resume.calls] == [False]


async def test_a_conversation_that_has_gone_does_not_fail_the_person() -> None:
    """They did what was asked. Losing the conversation is not their problem,
    and the login is still saved."""
    session = _Session()

    result = await _answer(
        session,
        _Browser(state={"cookies": COOKIES, "origins": []}),
        resume=_Resume(reached=False),
    )

    assert result.signed_in is True
    assert result.saved is True


# ---------------------------------------------------------------------------
# Which browser each half of the journey touches
#
# The feature has two browsers on purpose: the person signs in to the site's
# own (`login-<host>`), so a capture is bounded by the site they signed in to
# and the agent cannot drive the page while they type a password; the agent
# works in the conversation's own. Every bug below was the same bug -- a call
# that named the wrong one -- and the whole journey failed silently because of
# it, with "signed in with a saved login" in the transcript of a run that was
# signed out.
# ---------------------------------------------------------------------------


async def test_a_saved_login_is_loaded_into_the_agents_browser() -> None:
    """Not into the site's login browser, which this run does not use."""
    conversation = uuid4()
    session = _Session(logins=[_saved_login()])
    browser = _Browser()

    loaded, _ = await _service(session, browser).try_saved_login(
        origin=SITE, conversation_id=conversation, auth_ctx=_Ctx(uuid4())
    )

    assert loaded is True
    assert browser.loaded, "something was put into a browser"
    assert browser.loaded[0]["session"] == f"conv-{conversation.hex}"


async def test_the_browser_checked_is_the_browser_loaded() -> None:
    """Verifying the wrong one is how a dead session read as a live one.

    `_site_accepted` opens the site to see whether it bounced us to a login
    form. Opening the *login* browser answered that question about a browser
    the agent never uses -- and that browser had just had the cookies put into
    it, so the answer was always yes.
    """
    conversation = uuid4()
    session = _Session(logins=[_saved_login()])
    browser = _Browser()

    await _service(session, browser).try_saved_login(
        origin=SITE, conversation_id=conversation, auth_ctx=_Ctx(uuid4())
    )

    assert browser.opened, "the site was opened to see whether it let us in"
    assert browser.opened[0]["session"] == browser.loaded[0]["session"]


async def test_answering_hands_the_session_to_the_agents_browser() -> None:
    """The run resumes into its own browser, and it has to be signed in.

    Without this the person signs in, the capture is stored, the run resumes --
    and browses signed out, because what they did happened in a different
    Chrome. The next run would recover by loading the saved login, so the
    symptom was one wasted run and an agent reporting a login wall immediately
    after somebody had just got past one.
    """
    conversation = uuid4()
    session = _Session()
    browser = _Browser(state={"cookies": COOKIES, "origins": []})

    await _service(session, browser).answer(
        conversation_id=conversation,
        tool_call_id="call-1",
        user_id=OWNER,
        signed_in=True,
    )

    handed = [call for call in browser.loaded if call["session"]]
    assert handed, "the capture reached the agent's browser"
    assert handed[-1]["session"] == f"conv-{conversation.hex}"


# ---------------------------------------------------------------------------
# A link is a lookup, not a credential
# ---------------------------------------------------------------------------


async def test_a_stranger_cannot_read_what_somebody_is_being_asked_to_sign_in_to() -> (
    None
):
    """The ids in a sign-in URL name a pause; they do not grant access to it.

    A sign-in link goes out over WhatsApp and email, where it is forwarded and
    where unfurlers fetch it before any person does. If holding the two ids
    were enough, the origin and the agent's stated reason -- which site, and
    what it is being used for -- would be readable by anybody who had seen one.
    """
    found = await _service(_Session(), _Browser(), owner=uuid4()).pending(
        conversation_id=uuid4(), user_id=OWNER
    )

    assert found is None, "somebody else's pause is not readable"


async def test_a_stranger_cannot_answer_somebody_elses_sign_in() -> None:
    """Reading it is the smaller half. Answering resolves a pause in the
    owner's name: it tells their run to carry on, or declines on their behalf,
    and on the signed-in branch it captures whatever the browser holds and
    stores it as their login.
    """
    resume = _Resume()
    session = _Session()

    with pytest.raises(SignInNotPending):
        await _service(
            session,
            _Browser(state={"cookies": COOKIES, "origins": []}),
            resume,
            owner=uuid4(),
        ).answer(
            conversation_id=uuid4(),
            tool_call_id="call-1",
            user_id=OWNER,
            signed_in=True,
        )

    assert resume.calls == [], "no pause was resolved"
    assert session.logins == [], "nothing was stored against the caller"


async def test_a_conversation_that_is_gone_is_not_anybodys() -> None:
    """`None` from the owner lookup must not read as "no owner, so anyone"."""
    found = await _service(_Session(), _Browser(), owner=GONE).pending(
        conversation_id=uuid4(), user_id=OWNER
    )

    assert found is None
