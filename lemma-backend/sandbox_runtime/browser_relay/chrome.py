"""Finding, starting, and steering the Chrome this sandbox runs.

A live, *drivable* browser view needs Chrome's own protocol: the dashboard
`agent-browser` ships streams viewports and activity but has no input path, so
watching is all it can ever offer.

Three things make this awkward, and all three are handled here rather than by
whoever calls it.

**The port is not fixed.** Chrome writes it to ``DevToolsActivePort`` in the
profile directory on every launch. Forcing a fixed ``--remote-debugging-port``
instead does not work: ``agent-browser`` waits for that file and a forced port
stops it appearing, which breaks every other browser tool in the process.

**That file outlives the browser.** Chrome does not remove it on the way out,
and the browser leaves often -- ``agent-browser`` retires it after two idle
minutes, and the memory guard SIGKILLs it under pressure. So the file is a
record of where Chrome *was*, and reading it alone reports a port that nothing
is listening on. It cost a long debugging session: a viewer that asked to watch
a browser which had timed out got a connection error rather than "not running",
which surfaced as a 500 and, to the person clicking, as an unexplained failure.
Hence: the recorded port is a candidate, and it is not believed until something
answers on it.

**The port is not reachable from outside.** Chrome binds loopback, so CDP is
only ever reached *through* this process -- which is also the right answer for
safety, because it puts a place to stand between a viewer and full control of
the session.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
import re

import httpx

#: Chrome writes the port here on launch; the second line is the browser's own
#: WebSocket path, which is not what a page-level client wants.
_ACTIVE_PORT_FILE = Path("/tmp/lemma-browser/profile/DevToolsActivePort")

#: Where the image points every browser by default. One directory, so two
#: browsers cannot both use it.
_DEFAULT_PROFILE = "/tmp/lemma-browser/profile"

#: The session the image's own tooling uses, and the one that owns the default
#: profile directory.
DEFAULT_SESSION = os.environ.get("AGENT_BROWSER_SESSION", "workspace")


#: A session name is a path segment before it is anything else, so what may be
#: in one is decided here rather than trusted from whoever passed it. Letters,
#: digits, dot, dash and underscore: enough for `login-app.example.com`, and
#: nothing that means "parent directory" or "start again from the root".
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class UnsafeSessionName(ValueError):
    """A session name that cannot be part of a path."""


def is_safe_session(session: str) -> bool:
    """Whether this name may be used to build a profile directory.

    Refuses rather than sanitises. Quietly rewriting `../../etc` into something
    harmless would send the browser to a directory the caller did not ask for
    and report success, and two callers whose names differ only in the
    characters being stripped would silently share one profile.
    """
    return bool(_SAFE_SESSION.match(session)) and session not in {".", ".."}


def profile_for_session(session: str | None) -> str | None:
    """The profile directory a session's browser should use, if not the default.

    A session is a whole separate browser, and a browser needs a profile
    directory of its own -- Chrome locks the one it opens. The image points
    `AGENT_BROWSER_PROFILE` at a single path for every session, so a second
    session started without this exits immediately, before writing a port, and
    reports only "Chrome exited early". Which is exactly what a sign-in looked
    like the first time the whole flow was run for real.

    The name is validated *here*, where the path is built, rather than only in
    `session_for_domain` which derives one from a host. A caller may name a
    session directly, and a name that reached this unchecked would put the
    profile -- and the port file read from inside it -- anywhere on the disk.
    """
    if not session or session == DEFAULT_SESSION:
        return None
    if not is_safe_session(session):
        raise UnsafeSessionName(f"{session!r} cannot be part of a path")
    return f"{_DEFAULT_PROFILE}-{session}"


def active_port_file(session: str | None = None) -> Path:
    """Where a session's browser records its port.

    Inside the profile, so it moves with it. A session-aware profile and a
    fixed port file would mean reading the *default* browser's port and
    attaching a viewer to the wrong browser entirely.
    """
    profile = profile_for_session(session)
    return Path(profile) / "DevToolsActivePort" if profile else _ACTIVE_PORT_FILE


#: What `agent-browser get cdp-url` prints. Only the port is wanted: the rest of
#: that URL addresses the *browser* target, and a viewer wants a page.
_CDP_URL_PORT = re.compile(r"ws://127\.0\.0\.1:(\d+)/")

#: A cold start writes the config, brings up Xvfb and launches Chrome. Measured
#: at 18s in an idle container and over 90s in a sandbox that had just been
#: provisioned -- the image is amd64, so on an arm64 host every one of those
#: seconds is emulated, and the machine is busy with the rest of the sandbox at
#: the same time. 90s was the first guess and it was too low: it expired while
#: the browser was still coming up, so the viewer was told "not running" about a
#: browser that appeared moments later. Generous on purpose -- this bound exists
#: to stop a wedged start hanging forever, not to pace a healthy one.
_START_TIMEOUT_SECONDS = 240.0

#: Spelled absolutely, because **this process's PATH is not the agent's PATH**.
#:
#: Two things answer to `agent-browser` in this image: the raw npm binary in
#: `/opt/lemma-node/node_modules/.bin`, and the `lemma-node-tool` wrapper in
#: `/usr/local/bin` which runs `start-browser` first -- writing the config file
#: and starting Xvfb -- before handing over. An agent shell finds the wrapper.
#: A daemon does not: `/opt/lemma-node/node_modules/.bin` comes earlier on its
#: PATH, so the bare name resolves to the binary that cannot bootstrap, and in a
#: container where nothing has used the browser yet it fails with `config file
#: not found` -- which reads like a broken image rather than a missing
#: prerequisite. Naming the wrapper is what makes a cold sandbox work.
_AGENT_BROWSER = "/usr/local/bin/agent-browser"

#: Long enough to distinguish "refused" from "busy", short enough that a viewer
#: is not left waiting on a browser that has gone.
_PROBE_TIMEOUT_SECONDS = 2.0

#: How long to let the CLI exit on its own once it has answered, before killing
#: it. It has already given us the port by this point, so nobody is waiting on
#: the difference.
_REAP_TIMEOUT_SECONDS = 5.0

#: A CDP round trip on an already-open socket. Navigation itself is not waited
#: for here -- only the acknowledgement that the command was accepted.
_COMMAND_TIMEOUT_SECONDS = 30.0


class BrowserNotRunning(RuntimeError):
    """Chrome is not up, so there is nothing to attach to."""


def agent_browser_argv(*args: str, session: str | None = None) -> list[str]:
    """The CLI invocation, with the wrapper named absolutely.

    Falls back to the bare name only when the wrapper is absent, which is the
    case in a unit test with a stub on PATH and never in the shipped image.
    """
    executable = _AGENT_BROWSER if Path(_AGENT_BROWSER).exists() else "agent-browser"
    prefix: list[str] = []
    if session:
        prefix += ["--session", session]
        profile = profile_for_session(session)
        if profile:
            # Without its own profile the second browser cannot start at all.
            prefix += ["--profile", profile]
    return [executable, *prefix, *args]


def recorded_port(session: str | None = None) -> int:
    """The port Chrome last recorded, which it may well have left behind.

    Never use this without probing it -- see the module docstring. It is public
    only because "what does the file claim" is worth being able to ask.
    """
    try:
        first_line = active_port_file(session).read_text().splitlines()[0].strip()
        return int(first_line)
    except (OSError, IndexError, ValueError) as exc:
        raise BrowserNotRunning("the browser is not running") from exc


async def _answers_on(port: int) -> bool:
    """Whether anything is actually listening, as opposed to recorded."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except OSError, asyncio.TimeoutError:
        return False
    writer.close()
    # A close that fails tells us nothing about whether the port answered, and
    # it did: we are already holding the connection it opened.
    with suppress(OSError):
        await writer.wait_closed()
    return True


async def live_port(session: str | None = None) -> int:
    """Where Chrome is listening *now*, without starting it.

    This is the ambient answer: a workspace whose browser has been shed for
    idleness or memory is the ordinary resting state, and asking to look at it
    should not conjure one.
    """
    port = recorded_port(session)
    if not await _answers_on(port):
        raise BrowserNotRunning("the browser is not running")
    return port


async def ensure_port(*, session: str | None = None) -> int:
    """Where Chrome is listening, starting it if it is not.

    Asks ``agent-browser`` rather than launching Chrome directly, because that
    is the process which owns the browser's lifecycle: it knows the flags, the
    profile, and the daemon, and it is what every other browser tool in this
    image goes through. Its ``get cdp-url`` both starts the browser and reports
    where it landed, which is the whole job.

    This is the interactive answer, and the reason it is a separate function
    from `live_port` is cost: a cold start is tens of seconds and a few hundred
    megabytes in a sandbox where 220 MB free already triggers a kill. Somebody
    asking to take the wheel has asked for that. A card rendering in a
    transcript has not.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *agent_browser_argv("get", "cdp-url", session=session),
            stdout=asyncio.subprocess.PIPE,
            # Merged rather than a second pipe: one stream cannot deadlock
            # against the other filling its buffer, and when a start fails the
            # explanation and the output arrive in the order they happened.
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("could not run the browser CLI: %r", exc)
        raise BrowserNotRunning("the browser could not be started") from exc

    try:
        return await asyncio.wait_for(
            _read_port(process), timeout=_START_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        logging.getLogger(__name__).warning(
            "the browser did not start within %ss", _START_TIMEOUT_SECONDS
        )
        raise BrowserNotRunning("the browser could not be started") from exc
    finally:
        # The CLI has said what it came to say; it must not outlive the answer.
        # It normally exits on its own the moment it has printed the URL --
        # waiting for that is what keeps this from killing a healthy process
        # midway through its own cleanup -- and only a wedged one is killed.
        with suppress(ProcessLookupError, asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=_REAP_TIMEOUT_SECONDS)
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()


async def _read_port(process: asyncio.subprocess.Process) -> int:
    """The port from the CLI's output, read line by line rather than to EOF.

    **Never wait for this process's output to end.** `start-browser` leaves Xvfb
    and the browser daemon running, and they inherit the pipe -- so it stays open
    long after the CLI itself has exited, and anything that waits for EOF
    (`communicate()`, `read()`) waits forever. That is not a hypothetical
    either: it is the bug that made a takeover fail with an empty panel while
    the browser it was waiting for was up and healthy the whole time. Running
    the same command under `docker exec` hid it completely, because nothing
    there was capturing the output.
    """
    assert process.stdout is not None
    transcript: list[str] = []
    while True:
        raw = await process.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").strip()
        match = _CDP_URL_PORT.search(line)
        if match is not None:
            return int(match.group(1))
        # Bounded: a wedged CLI must not turn a start into a memory problem.
        if len(transcript) < 40:
            transcript.append(line)

    # Said in the log and not in the exception: why a browser would not start is
    # a sandbox-operations question, and the exception is reported to whoever
    # asked to watch -- the CLI's output is not theirs to read. Without this the
    # only symptom is an empty panel, which is what made this so expensive to
    # debug the first time.
    complaint = " | ".join(transcript) or "no output"
    logging.getLogger(__name__).warning("the browser did not start: %s", complaint)
    if _daemon_is_wedged(complaint):
        # One hung command leaves the daemon unable to answer, and it serves
        # every session in the sandbox -- so a person watching and the agent
        # working both get nothing until somebody clears it. Nothing did.
        await restart_daemon()
        raise BrowserNotRunning(
            "the browser daemon was not responding and has been restarted"
        )
    raise BrowserNotRunning("the browser could not be started")


#: What the CLI prints when its daemon has stopped answering. Matched on text
#: because that is all it gives us -- there is no exit code that distinguishes
#: a wedged daemon from a page that would not load.
_WEDGED = ("daemon may be busy", "unresponsive", "Resource temporarily unavailable")


def _daemon_is_wedged(complaint: str) -> bool:
    return any(marker.lower() in complaint.lower() for marker in _WEDGED)


async def restart_daemon() -> None:
    """Kill the browser daemon so the next command starts a fresh one.

    Best effort: this runs when something has already failed, and the caller
    has an error to report that matters more than this succeeding.
    """
    with suppress(OSError, asyncio.TimeoutError):
        process = await asyncio.create_subprocess_exec(
            "pkill",
            "-f",
            "agent-browser",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=_REAP_TIMEOUT_SECONDS)


async def page_targets(*, port: int) -> list[dict[str, str]]:
    """Chrome's page targets, newest first.

    Only pages: a service worker or an extension background target is not
    something a person can be shown, and offering one as a choice would be a
    way to pick a view that never paints.

    Takes the port rather than resolving it, so that the caller decides whether
    a missing browser should be started or reported.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"http://127.0.0.1:{port}/json")
            response.raise_for_status()
            targets = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Chrome went between the probe and the ask, which is a race the idle
        # timeout makes real rather than theoretical.
        raise BrowserNotRunning("the browser is not running") from exc
    return [
        {
            "id": str(target.get("id", "")),
            "title": str(target.get("title", "")),
            "url": str(target.get("url", "")),
        }
        for target in targets
        if target.get("type") == "page" and target.get("id")
    ]


def page_socket_url(target_id: str, *, port: int) -> str:
    """Where to attach for one page."""
    return f"ws://127.0.0.1:{port}/devtools/page/{target_id}"


async def open_url(url: str, *, session: str | None = None) -> None:
    """Point the browser at a page, starting it if it is not up.

    Goes through the CLI rather than CDP `Page.navigate` because the CLI owns
    tab bookkeeping for the session; navigating a target behind its back leaves
    it pointing at a page that is no longer there.

    This is what puts a person in front of the site they were asked to sign in
    to. Without it they arrive at whatever the browser last had open -- and in
    the common case, where the browser was retired for idleness and started
    fresh for their arrival, that is a blank page under a heading naming a site
    they cannot see.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *agent_browser_argv("open", url, session=session),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise BrowserNotRunning("the browser could not be started") from exc

    # Same rule as `_read_port`: the daemon inherits this pipe, so waiting for
    # EOF waits forever. Waiting on the process itself is safe -- `open` exits
    # once the page has been asked for.
    try:
        await asyncio.wait_for(process.wait(), timeout=_START_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        with suppress(ProcessLookupError):
            process.kill()
        raise BrowserNotRunning("the browser did not open the page") from exc
    finally:
        if process.stdout is not None:
            process.stdout.feed_eof()


async def keepalive(*, session: str | None = None) -> None:
    """Touch the browser so its idle timer does not retire it.

    `agent-browser` closes Chrome after two minutes without a *command*, and
    watching is not a command. So a person reading a page, or typing a password
    slowly, is idle by that measure and would have the browser shut under them.
    Any command resets the timer; asking for the URL is the cheapest one that
    does not change what is on screen.
    """
    with suppress(OSError, asyncio.TimeoutError):
        process = await asyncio.create_subprocess_exec(
            *agent_browser_argv("get", "url", session=session),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=_REAP_TIMEOUT_SECONDS)


class CdpConnection:
    """One CDP socket, with ids handed out and replies matched to them.

    Thin on purpose. The relay speaks a fixed handful of methods and never
    exposes this to a viewer, so there is no need for the full client every
    automation library ships.
    """

    def __init__(self, socket) -> None:
        self._socket = socket
        self._next_id = 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        message_id = self._next_id
        await self._socket.send(
            json.dumps({"id": message_id, "method": method, "params": params or {}})
        )
        while True:
            raw = await asyncio.wait_for(
                self._socket.recv(), timeout=_COMMAND_TIMEOUT_SECONDS
            )
            if isinstance(raw, bytes):
                continue
            message = json.loads(raw)
            if message.get("id") == message_id:
                if "error" in message:
                    raise BrowserNotRunning(f"{method} was refused: {message['error']}")
                return message.get("result") or {}

    async def send(self, method: str, params: dict | None = None) -> None:
        """Fire a command without waiting for its reply.

        For input and frame acknowledgements, where a round trip per keystroke
        would cost more than the reply is worth.
        """
        self._next_id += 1
        await self._socket.send(
            json.dumps({"id": self._next_id, "method": method, "params": params or {}})
        )
