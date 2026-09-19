"""The sign-in journey, end to end, against a real sandbox and a real browser.

Everything else about this feature is tested either side of a seam: the relay
against a fake Chrome, the service against a fake relay, the viewer's maths in
jsdom. All of those passed while the journey itself could not complete -- the
viewer opened the site in one browser session and attached the person to
another, nothing resolved the paused run, and a dead saved login reported
success for ever. Three green suites, one broken feature.

So this one runs the whole thing: a site that really checks a cookie, a real
Chromium in a real container, a real capture, and a second run that reuses what
the first one kept.

The site is served from inside the sandbox. A public login page would make this
test depend on somebody else's uptime, their bot detection, and a password we
would have to store -- and the thing under test is our half, not theirs.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import ExecCommandRequest
from app.modules.agent.tools.workspace_cli.workspace_cli import exec_command_internal
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.workspace, pytest.mark.timeout(900)]

SITE_PORT = 18099
SITE = f"http://127.0.0.1:{SITE_PORT}"

#: A site whose only interesting property is that it checks a cookie.
#:
#: Signed out it serves a form and the word "Sign in"; signed in it serves
#: "Welcome back". That is exactly the distinction `_site_accepted` has to make,
#: and the distinction a capture has to preserve across a restart.
SITE_SOURCE = """
import http.server, urllib.parse

class Site(http.server.BaseHTTPRequestHandler):
    def _send(self, body, status=200, headers=()):
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        signed_in = "demo_session=yes" in (self.headers.get("Cookie") or "")
        if signed_in:
            self._send("<html><head><title>Account</title></head>"
                       "<body><h1>Welcome back</h1></body></html>")
            return
        self._send(
            "<html><head><title>Sign in</title></head><body>"
            "<form method='POST' action='/login'>"
            "<input name='who' id='who'>"
            "<button type='submit' id='go'>Sign in</button>"
            "</form></body></html>"
        )

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        urllib.parse.parse_qs(self.rfile.read(length).decode())
        self._send(
            "<html><head><title>Account</title></head>"
            "<body><h1>Welcome back</h1></body></html>",
            headers=[("Set-Cookie", "demo_session=yes; Path=/; Max-Age=86400")],
        )

    def log_message(self, *args):
        pass

# Threading, and that is load-bearing rather than tidiness. Two browsers
# reach this site now -- the one the person signs in to and the one the
# agent works in -- and a single-threaded server serves one connection at a
# time. A browser holding a keep-alive connection open therefore blocked the
# other's request until the CLI gave up, which surfaced as "CDP command timed
# out: Page.navigate" and reads exactly like a browser fault.
http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Site).serve_forever()
"""


async def _context(authenticated_client, fixed_test_org, fixed_test_user):
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Sign-in Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod = response.json()
    ctx = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(pod["id"]),
        conversation_id=uuid4(),
        agent_name="signing_in_e2e",
        workload_type="agent",
    )

    async def warmup():
        return await exec_command_internal(
            ctx, ExecCommandRequest(cmd="true", timeout_seconds=180)
        )

    await eventually(
        label="real workspace sandbox warmup",
        probe=warmup,
        done=lambda result: result.success,
        timeout_seconds=300,
        interval_seconds=2.0,
    )
    return ctx


async def _run(ctx, command: str, *, timeout: int = 180):
    result = await exec_command_internal(
        ctx, ExecCommandRequest(cmd=command, timeout_seconds=timeout)
    )
    assert result.success, getattr(result, "error", result)
    return result


async def _serve_the_site(ctx) -> None:
    """Put a cookie-checking site inside the sandbox and wait for it to answer."""
    source = SITE_SOURCE.replace("PORT", str(SITE_PORT))
    await _run(
        ctx,
        f"cat > /tmp/demo_site.py <<'PY'\n{source}\nPY\n"
        "setsid nohup python3 /tmp/demo_site.py >/tmp/demo_site.log 2>&1 </dev/null &",
    )

    async def probe():
        return await exec_command_internal(
            ctx,
            ExecCommandRequest(
                cmd=f"curl -sf -o /dev/null -w '%{{http_code}}' {SITE}/",
                timeout_seconds=20,
            ),
        )

    await eventually(
        label="the demo site answering",
        probe=probe,
        done=lambda result: "200" in (result.stdout or ""),
        timeout_seconds=60,
        interval_seconds=1.0,
    )


def _service(db_manager, *, waiting_on: str | None = None, owner: UUID | None = None):
    """The real service, against the real database and the real sandbox.

    Two stand-ins, and both for the same reason: there is no conversation row.
    This test's subject is *which browser* a sign-in lands in -- the thing that
    was silently wrong -- so it drives the service directly rather than through
    an agent run, and its `conversation_id` names nothing in the database.

    So the pause reader is stood in for, because there is no paused tool call
    for the real one to find, and the owner lookup is stood in for, because the
    real one reads the conversation and would find nothing to own. Standing in
    for the second is what says a sign-in is answerable only by the person whose
    it is -- the real check, and what a pause looks like, are covered where they
    belong: `web_login/tests/unit/test_sign_in_service.py`.
    """
    from app.modules.web_login.contracts import SignInService

    async def _waiting(_uow, _conversation_id):
        if waiting_on is None:
            return None
        return SimpleNamespace(
            tool_call_id=waiting_on,
            kind="browser_sign_in",
            tool_args={"origin": SITE, "reason": "reading the account page"},
            agent_run_id=None,
        )

    async def _owned_by(_uow, _conversation_id):
        return owner

    return SignInService(
        SessionUnitOfWorkFactory(db_manager.session_factory),
        read_pause=_waiting,
        owner_of=_owned_by,
    )


class _Ctx:
    """An authorization context for the person whose sandbox this is."""

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        self.delegated_by_user_id = None
        self.is_user_equivalent = False
        self.pod_id = None

    async def require(self, *_args, **_kwargs) -> None:
        return None


async def test_a_person_signs_in_once_and_the_next_run_does_not_ask(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    db_manager,
) -> None:
    """Ask, sign in, keep it, reuse it, remove it, be asked again.

    Every step here was green in isolation while the whole was broken, which is
    why this exists as one test rather than five.
    """
    del local_sandbox_server, backend_server, configure_workspace_api_url
    ctx = await _context(authenticated_client, fixed_test_org, fixed_test_user)
    user_id = UUID(fixed_test_user["id"])
    auth = _Ctx(user_id)
    service = _service(db_manager)

    await _serve_the_site(ctx)

    # Start signed out, and arrange it rather than assume it. The profile is
    # durable and one sandbox serves the whole module, so a sibling test --
    # or an earlier run of this one against a resumed sandbox -- leaves this
    # site signed in. That is the feature working; it just means a test whose
    # first assertion is "nobody is signed in" has to make that true.
    await _run(
        ctx, "agent-browser open about:blank && agent-browser cookies clear || true"
    )

    try:
        # 1. Nothing signed in, so the person has to be asked.
        assert not await service.already_signed_in(origin=SITE, auth_ctx=auth)

        # 2. The ask. Nothing is written: the paused tool call is the record
        #    of what was asked, and the browser is opened now so somebody who
        #    clicks straight away finds the page already there.
        tool_call_id = f"call_{uuid4().hex[:8]}"
        site = await service.open_request(
            origin=SITE,
            reason="reading the account page",
            auth_ctx=auth,
            conversation_id=ctx.conversation_id,
            tool_call_id=tool_call_id,
        )
        assert site.rstrip("/") == SITE

        # 3. The person signs in. No environment juggling: there is one
        #    browser, so the CLI, the relay and whatever a person is watching
        #    are all the same Chrome. The three-way pairing this used to have
        #    to arrange -- a login session, an agent session, and a capture
        #    read from whichever of them was right -- is what went.
        signed = await _run(
            ctx,
            f"agent-browser open {SITE}/ && "
            "agent-browser find role button click --name 'Sign in' && "
            "sleep 1 && agent-browser get title",
            timeout=240,
        )
        assert "Account" in (signed.stdout or ""), signed.stdout

        # 4. Answering resumes the run and reports what the site looks like.
        #    Answered the way the page answers: by naming the pause.
        finished = await _service(
            db_manager, waiting_on=tool_call_id, owner=user_id
        ).answer(
            conversation_id=ctx.conversation_id,
            tool_call_id=tool_call_id,
            user_id=user_id,
            signed_in=True,
        )
        assert finished.working is True

        # 5. The load-bearing one. End the browser the way production ends
        #    it, and the login survives -- because it is in a profile on the
        #    durable disk rather than in a capture somebody has to read back
        #    correctly.
        #
        #    "The way production ends it" is doing the work, and it took
        #    three measurements on a real sandbox to get right. Chrome
        #    batches its cookie store to disk on a 30 second timer, so how
        #    the browser stops decides whether a login made a second ago is
        #    still there:
        #
        #        agent-browser close --all   keeps it
        #        SIGTERM to all 11 processes loses it
        #        SIGTERM to the browser process alone, exiting cleanly in
        #                                    half a second -- loses it
        #        SIGKILL                     loses it
        #
        #    A signal does not flush the queue; a shutdown through CDP does.
        #    So every path that stops this browser closes it first --
        #    `shed_browser` before it signals, and an E2B release before it
        #    pauses -- and this asserts the guarantee that is actually
        #    shipped rather than one that sounded right.
        #
        #    Then Xvfb, to prove the display is rebuilt too: `agent-browser`
        #    brings its own back, which is why nothing here runs
        #    `lemma-ensure-display` to recover.
        closed = await _run(ctx, "agent-browser close --all ; pkill -x Xvfb || true")
        assert "Closed" in (closed.stdout or ""), closed.stdout

        # Diagnose before asserting. `already_signed_in` answers one bool for
        # two very different failures -- the cookie went, or the browser did
        # not come back -- and the bool alone sent this round three wrong
        # fixes.
        reopened = await _run(
            ctx, f"agent-browser open {SITE}/ && agent-browser get title", timeout=240
        )
        assert "Account" in (reopened.stdout or ""), reopened.stdout
        assert await service.already_signed_in(origin=SITE, auth_ctx=auth)

        # 6. Forgetting it really signs the browser out, over the route the
        #    saved-logins screen calls. Its predecessor deleted an encrypted
        #    copy and left the browser signed in, so this assertion could not
        #    have been written against it.
        removed = await authenticated_client.request(
            "DELETE", "/web-logins", params={"origin": site}
        )
        assert removed.status_code == status.HTTP_200_OK, removed.text
        assert removed.json()["forgotten"] is True

        assert not await service.already_signed_in(origin=SITE, auth_ctx=auth)
    finally:
        await service.close()
