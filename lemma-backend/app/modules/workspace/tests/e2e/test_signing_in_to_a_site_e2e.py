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

import shlex
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


def _service(db_manager, *, waiting_on: str | None = None):
    """The real service, against the real database and the real sandbox.

    The one stand-in is the pause reader, and only when the test needs to answer
    one. This test's subject is *which browser* a sign-in lands in -- the thing
    that was silently wrong -- and it drives the service directly rather than
    through an agent run, so there is no paused tool call for the real reader to
    find. What a pause looks like, and that answering resolves it, is covered
    where it belongs: `web_login/tests/unit/test_sign_in_service.py`.
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

    return SignInService(
        SessionUnitOfWorkFactory(db_manager.session_factory),
        read_pause=_waiting,
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

    try:
        # 1. Nothing saved, so there is nothing to reuse.
        loaded, detail = await service.try_saved_login(
            origin=SITE, conversation_id=ctx.conversation_id, auth_ctx=auth
        )
        assert loaded is False, detail

        # 2. The ask. This opens the site in the session the capture will read,
        #    which is the pairing the viewer used to get wrong. Nothing is
        #    written: the paused tool call is the record of what was asked.
        tool_call_id = f"call_{uuid4().hex[:8]}"
        site = await service.open_request(
            origin=SITE,
            reason="reading the account page",
            auth_ctx=auth,
            conversation_id=ctx.conversation_id,
            tool_call_id=tool_call_id,
        )
        assert site.rstrip("/") == SITE

        # 3. The person signs in. Driven here with the CLI in the *login*
        #    session -- the same browser `ensure_for_sign_in` opened and the same
        #    one `finish` reads. If those three ever disagree again, this fails.
        from app.modules.workspace.domain.browser_context import agent_session
        from sandbox_runtime.browser_relay.chrome import profile_for_session
        from sandbox_runtime.browser_relay.state import session_for_domain

        def _in(session: str) -> str:
            """Run the CLI in one named browser, the way each caller does."""
            profile = profile_for_session(session)
            return (
                f"export AGENT_BROWSER_SESSION={shlex.quote(session)} "
                f"AGENT_BROWSER_PROFILE={shlex.quote(profile or '')} ; "
            )

        #: Where the person signs in.
        env = _in(session_for_domain("127.0.0.1"))
        #: Where the *agent* works -- a different Chrome with its own profile,
        #: and the one every assertion about "is it signed in" has to use.
        #: Checking the login browser instead is how this test passed while the
        #: feature was broken: it proved the browser the person signed into was
        #: signed in, which was never in doubt.
        agent_env = _in(agent_session(ctx.conversation_id))
        signed = await _run(
            ctx,
            f"{env} agent-browser open {SITE}/ && "
            "agent-browser find role button click --name 'Sign in' && "
            "sleep 1 && agent-browser get title",
            timeout=240,
        )
        assert "Account" in (signed.stdout or ""), signed.stdout

        # 4. Keeping it. `finish` refuses an empty capture, so reaching
        #    `saved is True` means real cookies came back from a real browser.
        # Answered the way the page answers: by naming the pause, not a row.
        # There is no request id to pass, because there is no request row --
        # the paused tool call is the record.
        finished = await _service(db_manager, waiting_on=tool_call_id).answer(
            conversation_id=ctx.conversation_id,
            tool_call_id=tool_call_id,
            user_id=user_id,
            signed_in=True,
        )
        assert finished.saved is True, finished.saved_detail

        # 4b. And the run it resumes into is signed in *now*, in the agent's own
        #     browser. Without the hand-over this is the assertion that fails:
        #     the person signed in to one Chrome and the agent carried on in
        #     another, which had never seen the site.
        resumed = await _run(
            ctx, f"{agent_env} agent-browser open {SITE}/ ; agent-browser get title"
        )
        assert "Account" in (resumed.stdout or ""), resumed.stdout

        # 5. A later run reuses it without asking. The browser is wiped first,
        #    so this cannot pass on cookies left lying in the profile -- it has
        #    to come from what was stored and injected.
        await _run(ctx, "pkill -x Xvfb || true ; rm -rf /tmp/lemma-browser* || true")
        loaded, detail = await service.try_saved_login(
            origin=SITE, conversation_id=ctx.conversation_id, auth_ctx=auth
        )
        assert loaded is True, detail

        # 6. And the *agent's* browser really is signed in, not merely loaded.
        #    Two failures hide behind the wrong env here: a session the site had
        #    rejected reported as working, and -- the one that made the whole
        #    feature a no-op -- a session loaded into the login browser while
        #    the agent worked in its own.
        landed = await _run(
            ctx, f"{agent_env} agent-browser open {SITE}/ ; agent-browser get title"
        )
        assert "Account" in (landed.stdout or ""), landed.stdout

        # 7. Removing it makes the next run ask again. Removed the way a person
        #    removes one -- over the route the saved-logins screen calls -- and
        #    not by reaching into a repository, which is no longer something a
        #    caller outside `web_login` can do.
        removed = await authenticated_client.request(
            "DELETE", "/web-logins", params={"origin": site}
        )
        assert removed.status_code == status.HTTP_200_OK, removed.text

        loaded, detail = await service.try_saved_login(
            origin=SITE, conversation_id=ctx.conversation_id, auth_ctx=auth
        )
        assert loaded is False, detail
    finally:
        await service.close()
