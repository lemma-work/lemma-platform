"""Finding, starting, and steering the Chrome this sandbox runs.

A live, drivable view of it is not this module's job any more. Two earlier
designs lived here in turn: driving CDP's `Page.startScreencast` and
`Input.dispatch*` directly, then proxying `agent-browser`'s own session-scoped
stream server (`stream_port`, and the JPEG frame protocol -- both gone;
`stream_proxy.py` survives under its old name, now carrying RFB bytes for
`app.py`'s `/vnc` route rather than frames). What replaced
both is `x11vnc` and `websockify` in front of the Xvfb display Chrome already
runs on: a real screen rather than a translated one, so there is no frame
protocol, no viewport measurement and no coordinate space for this module to
answer questions about any more. `app.py`'s `/vnc` route talks to that
directly; what is left here is Chrome's own lifecycle: where it is, whether
it is up, and how to point it at a page. Three things make that awkward, and
all three are handled here rather than by whoever calls it.

**The port is not fixed.** Chrome writes it to ``DevToolsActivePort`` in the
profile directory on every launch. Forcing a fixed ``--remote-debugging-port``
instead does not work: ``agent-browser`` waits for that file and a forced port
stops it appearing, which breaks every other browser tool in the process.

**That file outlives the browser.** Chrome does not remove it on the way out,
and the browser leaves often -- ``agent-browser`` retires it after five idle
minutes, and the memory guard SIGKILLs it under pressure. So the file is a
record of where Chrome *was*, and reading it alone reports a port that nothing
is listening on. It cost a long debugging session: a viewer that asked to watch
a browser which had timed out got a connection error rather than "not running",
which surfaced as a 500 and, to the person clicking, as an unexplained failure.
Hence: the recorded port is a candidate, and it is not believed until something
answers on it.

**Nothing here is reachable from outside.** Chrome binds loopback, and so do
`x11vnc` and `websockify` -- all reached *through* this process, which is also
the right answer for safety: it puts a place to stand between a viewer and the
browser, and it means no new port is published.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import logging
import os
from pathlib import Path
import re
import socket
import tempfile
import weakref

import httpx

from sandbox_runtime.paths import BROWSER_PROFILE, sandbox_command

#: The browser, and the person's logins. Durable -- see ``paths.py``.
_DEFAULT_PROFILE = BROWSER_PROFILE

#: Chrome writes the port here on launch; the second line is the browser's own
#: WebSocket path, which is not what a page-level client wants.
_ACTIVE_PORT_FILE = Path(_DEFAULT_PROFILE) / "DevToolsActivePort"

#: Where an explicitly-named session's profile goes, and it is deliberately not
#: the durable one. Naming a session is how an agent asks for a *second*,
#: separate browser -- two signed-in users side by side, say -- and that is a
#: scratch thing by construction. Under ``/tmp`` so it dies with the sandbox
#: rather than accumulating profiles in the person's home, one per name anyone
#: ever passed.
_SCRATCH_PROFILE_BASE = "/tmp/lemma-browser/profile"

#: The session the image's own tooling uses, and the one that owns the default
#: profile directory.
#:
#: **Fixed, not read from `AGENT_BROWSER_SESSION`.** It used to be read from the
#: environment, which is the same name the agent's browser script exports into
#: its conversation's shell -- and that shell is persistent, so the export
#: outlives the command. The relay is started by an exec into that sandbox and
#: inherited it, whereupon it believed a *conversation's* session was the
#: default one: `profile_for_session` returned `None` for it, the port file was
#: read from the default profile, and every viewer was told "the browser is not
#: running" about a browser that was running perfectly well a few lines above in
#: the same log. One relay serves every conversation in a sandbox, so its idea
#: of "the default" cannot be whichever conversation happened to start it. It
#: matches `AGENT_BROWSER_SESSION` in `Dockerfile.workspace`, which is the
#: image's own default and the session a bare `agent-browser` lands in.
DEFAULT_SESSION = "workspace"


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


def profile_for_session(session: str | None) -> str | None:  # noqa: D401
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
    # The directory is named from a *digest* of the session, not from the
    # session itself. Validating the name and then interpolating it would work,
    # and still leaves a path built out of a caller's string -- one refactor
    # away from the check being bypassed, and not something a reader (or a
    # scanner) can confirm by looking at this line. A hex digest has no
    # separators, no dots and no way to climb out, whatever it was made from.
    #
    # Nobody reads this directory's name: the *session* keeps the readable
    # `login-app.example.com`, and that is what appears in commands and logs.
    fingerprint = hashlib.sha256(session.encode()).hexdigest()[:32]
    return f"{_SCRATCH_PROFILE_BASE}-{fingerprint}"


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
#: at 2.7s in a fresh container on a native image; it was over 90s when the
#: image was built for amd64 and emulated, which is where the first guess of 90s
#: came from and why it expired while the browser was still coming up -- the
#: viewer was told "not running" about a browser that appeared moments later.
#: Still generous, because this bound exists to stop a wedged start hanging for
#: ever rather than to pace a healthy one, and a just-provisioned sandbox is
#: busy with the rest of its own startup.
_START_TIMEOUT_SECONDS = 240.0

#: Spelled absolutely, because **this process's PATH is not the agent's PATH**.
#:
#: Two things answer to `agent-browser` in this image: the raw npm binary in
#: `/opt/lemma-node/node_modules/.bin`, and the `lemma-node-tool` wrapper --
#: the overlay's copy, or the image's in `/usr/local/bin` -- which runs
#: `start-browser` first -- writing the config file
#: and starting Xvfb -- before handing over. An agent shell finds the wrapper.
#: A daemon does not: `/opt/lemma-node/node_modules/.bin` comes earlier on its
#: PATH, so the bare name resolves to the binary that cannot bootstrap, and in a
#: container where nothing has used the browser yet it fails with `config file
#: not found` -- which reads like a broken image rather than a missing
#: prerequisite. Naming the wrapper is what makes a cold sandbox work.
_AGENT_BROWSER = "agent-browser"

#: Long enough to distinguish "refused" from "busy", short enough that a viewer
#: is not left waiting on a browser that has gone.
_PROBE_TIMEOUT_SECONDS = 2.0

#: How long to let the CLI exit on its own once it has answered, before killing
#: it. It has already given us the port by this point, so nobody is waiting on
#: the difference.
_REAP_TIMEOUT_SECONDS = 5.0


class BrowserNotRunning(RuntimeError):
    """Chrome is not up, so there is nothing to attach to."""


def agent_browser_argv(*args: str, session: str | None = None) -> list[str]:
    """The CLI invocation, with the wrapper named absolutely.

    Falls back to the bare name only when the wrapper is absent, which is the
    case in a unit test with a stub on PATH and never in the shipped image.
    """
    wrapper = sandbox_command(_AGENT_BROWSER)
    executable = wrapper if Path(wrapper).exists() else _AGENT_BROWSER
    prefix: list[str] = []
    if session:
        prefix += ["--session", session]
        profile = profile_for_session(session)
        if profile:
            # Without its own profile the second browser cannot start at all.
            prefix += ["--profile", profile]
    return [executable, *prefix, *args]


def agent_browser_env(session: str | None = None) -> dict[str, str]:
    """The environment to run the CLI in, with this session's names spelled out.

    The flags above are not enough on their own. The `agent-browser` wrapper
    is a wrapper that bootstraps a cold sandbox by running `start-browser`, and
    that script reads `AGENT_BROWSER_SESSION` and `AGENT_BROWSER_PROFILE` from
    its environment -- it never sees the flags. So a relay that inherited one
    conversation's exports would hand the flags one session and bootstrap
    Chrome in another's profile.

    Overriding rather than clearing: everything else in the environment (DISPLAY,
    the config path, the idle timeout, the stream's caps) is the image's, and is
    what this is meant to run with.
    """
    profile = profile_for_session(session) or _DEFAULT_PROFILE
    return {
        **os.environ,
        "AGENT_BROWSER_SESSION": session or DEFAULT_SESSION,
        "AGENT_BROWSER_PROFILE": profile,
    }


def recorded_port(session: str | None = None) -> int:
    """The port Chrome last recorded, which it may well have left behind.

    Never use this without probing it -- see the module docstring. It is public
    only because "what does the file claim" is worth being able to ask.
    """
    path = active_port_file(session)
    try:
        first_line = path.read_text().splitlines()[0].strip()
        return int(first_line)
    except (OSError, IndexError, ValueError) as exc:
        # Named, because "the browser is not running" covers two very different
        # facts -- never started, and started somewhere this cannot see -- and
        # they reach a person as the same four-digit close code. The distinction
        # is only ever visible in this sentence.
        raise BrowserNotRunning(f"no port recorded at {path} ({exc!r})") from exc


async def answers_on(port: int) -> bool:
    """Whether anything is actually listening, as opposed to recorded.

    Public because the relay asks it of websockify's port as well: "the
    bridge script exited 0" and "a viewer will get a picture" are different
    claims, and this is the one that answers the second.
    """
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

    **Two places are asked, because Chrome does not always use the profile it
    was given.** `agent-browser` can run Chrome on a throwaway
    `--user-data-dir=/tmp/agent-browser-chrome-<uuid>` and copy the profile
    back on close -- measured, and what a bare `agent-browser open` does --
    and Chrome writes `DevToolsActivePort` into whichever directory it is
    actually using. So the configured profile's copy can name a launch that
    ended, while the live browser is recorded somewhere else entirely:

        ensure 1: recorded=45007 live=40977 recorded_answers=no
        ensure 2: recorded=40977 live=42989 recorded_answers=no

    `lemma-ensure-display` no longer provokes that, but the fix is in the
    image and this is in the runtime bundle -- which is installed on every
    session, so it reaches sandboxes the image has not reached yet. It is
    also the more durable half: it holds whatever agent-browser decides to do
    next.
    """
    for port in _candidate_ports(session):
        if await answers_on(port):
            return port
    raise BrowserNotRunning("nothing answers on any recorded port")


def _candidate_ports(session: str | None) -> list[int]:
    """Every port a Chrome in this sandbox could have recorded, best first.

    The configured profile first, because that is where Chrome writes when
    it is given one and used it. Then agent-browser's own scratch profiles,
    newest first -- there is normally at most one, and a stale directory
    whose port answers nothing costs a refused connection to rule out.

    **The scratch sweep is for the default session only, and that is a
    boundary rather than an optimisation.** A scratch directory is named
    `agent-browser-chrome-<uuid>` and records nothing about whose browser it
    is, so a named session whose recorded port had gone stale would pick up
    whatever scratch Chrome was newest -- and the named session that exists
    in this product is `login-<host>`, the one a person types a password
    into. `/targets` would list its pages and `/profile:forget` would clear
    its data, both while naming a different session.

    The literal `/tmp` is deliberate, not an oversight flagged by a linter:
    this is not a temporary file this process creates, it is where
    agent-browser was *measured* to put its scratch profiles.
    `tempfile.gettempdir()` honours `TMPDIR`, so a relay started with one
    set would look somewhere the browser never writes and quietly find
    nothing.

    Nothing is lost by the restriction. The sweep exists for images that
    predate this branch, where `lemma-ensure-display` ended in a bare
    `agent-browser open` and stranded the port file -- and that is the
    *default* browser's path. Every named session is started from this
    module, through `agent_browser_argv(..., session=...)` with a URL, which
    keeps Chrome on the configured profile and so keeps its recorded port
    accurate. A named session that cannot be found by its own port file is
    genuinely not running, and saying so is the honest answer.
    """
    found: list[int] = []
    with suppress(BrowserNotRunning):
        found.append(recorded_port(session))
    if session is not None and session != DEFAULT_SESSION:
        return found
    scratch = sorted(
        Path("/tmp").glob("agent-browser-chrome-*/DevToolsActivePort"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for path in scratch[:4]:
        try:
            port = int(path.read_text().splitlines()[0].strip())
        except OSError, IndexError, ValueError:
            continue
        if port not in found:
            found.append(port)
    return found


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

    **One start per session at a time, and a browser that is already up is
    not started again.** Several callers arrive at once whenever somebody opens
    the view -- the view socket, the toolbar's targets poll, the keepalive --
    and two CLIs that each find no daemon each spawn one. The second daemon's
    Chrome finds the profile locked by the first, hands off and exits, and the
    daemon dies with "exited during startup"; the orphan later deletes the
    *live* daemon's socket and pid file on its way out, so the next command
    spawns yet another, and the browser stays up and unreachable until the
    idle timer kills it. Reproduced on demand, twice. Hence the lock, and the
    `live_port` check *inside* it: whoever waited is usually waiting for the
    browser the caller ahead of it has just started.

    **A collided or wedged daemon is cleared and the start tried once more**,
    rather than reported: the person opening the view would otherwise see a
    refusal for a state nobody but this function can get them out of.
    """
    async with start_lock(session):
        with suppress(BrowserNotRunning):
            return await live_port(session)
        clear_foreign_lock(Path(profile_for_session(session) or _DEFAULT_PROFILE))
        try:
            return await _start(session)
        except _DaemonWedged:
            await restart_daemon(session=session)
        try:
            return await _start(session)
        except _DaemonWedged as exc:
            # Reported rather than recovered a second time: another round
            # straight after the first would only kill what the retry started.
            raise BrowserNotRunning(
                "the browser daemon was not responding, and a restart did not help"
            ) from exc


def clear_foreign_lock(profile: Path, *, host: str | None = None) -> bool:
    """Remove a profile lock left by another container; whether one was.

    `SingletonLock` links to `<hostname>-<pid>`, and Chrome checks the pid only
    when the host is its own. The profile is on the home volume, which outlives
    the container -- a reset, an image upgrade, one removed under a running
    browser -- and Chrome takes a lock naming the old host as live: it shows a
    "profile in use on another computer" dialog on the display and never
    starts, so every start after it waited out the full timeout. No browser on
    this host can hold a lock naming another, so the three files go.
    `DevToolsActivePort` stays: see `lemma-ensure-display`, which does the same
    for a start that comes from an agent's shell.
    """
    lock = profile / "SingletonLock"
    try:
        owner = os.readlink(lock)
    except OSError:
        return False
    here = host or socket.gethostname()
    if not here or owner.startswith(f"{here}-"):
        return False
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        with suppress(OSError):
            (profile / name).unlink()
    logging.getLogger(__name__).warning(
        "cleared a browser profile lock left by another host (%s)", owner
    )
    return True


#: One lock per session, per event loop. The relay runs a single loop, so in
#: production this is just "per session"; the loop is in the key because an
#: `asyncio.Lock` belongs to the loop it was first contended on, and a test
#: suite runs a fresh loop per test.
_START_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, asyncio.Lock]
] = weakref.WeakKeyDictionary()


def start_lock(session: str | None) -> asyncio.Lock:
    """The lock held by anything in this relay that can start a session's browser.

    Public because `ensure_port` is not the only such thing: `open` and the
    keepalive's `get url` each spawn a daemon when none answers, and racing
    either against a start is the same collision.
    """
    locks = _START_LOCKS.setdefault(asyncio.get_running_loop(), {})
    return locks.setdefault(session or DEFAULT_SESSION, asyncio.Lock())


async def _start(session: str | None) -> int:
    """Run ``get cdp-url`` once and return the port it reports."""
    try:
        process = await asyncio.create_subprocess_exec(
            *agent_browser_argv("get", "cdp-url", session=session),
            env=agent_browser_env(session),
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


class _DaemonWedged(BrowserNotRunning):
    """The daemon cannot answer, or died starting: clearing it may help."""


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
        # working both get nothing until somebody clears it. `ensure_port`
        # does, and tries again.
        raise _DaemonWedged("the browser daemon was not responding")
    raise BrowserNotRunning("the browser could not be started")


#: What the CLI prints when its daemon has stopped answering, or died before
#: it could. Matched on text because that is all it gives us -- there is no
#: exit code that distinguishes a wedged daemon from a page that would not load.
#:
#: "exited during startup" is the collision `ensure_port` describes: a second
#: daemon whose Chrome found the profile locked by one still running. It is in
#: this list because the cure is the same -- nothing improves until the daemons
#: *and* that Chrome are gone.
_WEDGED = (
    "daemon may be busy",
    "unresponsive",
    "Resource temporarily unavailable",
    "exited during startup",
)


def _daemon_is_wedged(complaint: str) -> bool:
    return any(marker.lower() in complaint.lower() for marker in _WEDGED)


#: How long a stopped process gets to go on its own before it is killed. Long
#: enough for Chrome to release its profile lock cleanly; a person is waiting
#: on the far side of this, so not much longer.
_STOP_GRACE_SECONDS = 3.0


async def restart_daemon(*, session: str | None = None) -> None:
    """Stop the browser daemons, and the Chrome on this session's profile.

    Both, because either alone leaves the collision in place. This used to
    stop only the daemons, and Chrome outlives its daemon -- that orphan is
    what holds the profile lock and makes every fresh daemon's Chrome hand off
    and exit. A daemon left running is the other half: it deletes the next
    one's socket when it does exit. So this waits for both to be *gone*, not
    merely signalled; one still shutting down when the retry starts would
    delete the new daemon's socket all the same.

    Chrome is matched by its ``--user-data-dir``, anchored at the end so the
    default profile can never match a named session's ``profile-<digest>``.
    Killing it is not clean, which is why `lemma-ensure-display` passes
    ``--hide-crash-restore-bubble``: the next start would otherwise greet the
    person with "Restore pages?".

    Best effort: this runs when something has already failed, and the caller
    has an error to report that matters more than this succeeding.
    """
    profile = profile_for_session(session) or _DEFAULT_PROFILE
    # pkill's patterns are extended regexes, and a profile path is only ever
    # `[A-Za-z0-9/._-]`, so the dot is the one character that needs taming.
    chrome = f"--user-data-dir={profile.replace('.', '[.]')}([[:space:]]|$)"
    for pattern in (_AGENT_BROWSER, chrome):
        await _stop_matching(pattern)


async def _stop_matching(pattern: str) -> None:
    """SIGTERM what matches, wait for it to go, SIGKILL whatever has not."""
    loop = asyncio.get_running_loop()
    await _pkill("-TERM", pattern)
    deadline = loop.time() + _STOP_GRACE_SECONDS
    while await _any_matching(pattern):
        if loop.time() >= deadline:
            await _pkill("-KILL", pattern)
            return
        await asyncio.sleep(0.1)


async def _pkill(signal: str, pattern: str) -> None:
    with suppress(OSError, asyncio.TimeoutError):
        process = await asyncio.create_subprocess_exec(
            "pkill",
            signal,
            "-f",
            # The Chrome pattern starts with `--`, which would otherwise be
            # read as one of pkill's own options.
            "--",
            pattern,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=_REAP_TIMEOUT_SECONDS)


async def _any_matching(pattern: str) -> bool:
    """Whether anything still matches.

    A probe that cannot answer says no: it only decides whether to keep
    waiting, and waiting on a missing or hung tool is waiting for ever.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "pgrep",
            "-f",
            "--",
            pattern,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return False
    try:
        found = await asyncio.wait_for(process.wait(), timeout=_REAP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(ProcessLookupError):
            await process.wait()
        return False
    return found == 0


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

    Under `start_lock`, because `open` starts a daemon when none answers and so
    collides with a concurrent `ensure_port` exactly as two starts do.
    """
    async with start_lock(session):
        await _open_url(url, session=session)


async def _open_url(url: str, *, session: str | None) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *agent_browser_argv("open", url, session=session),
            env=agent_browser_env(session),
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


#: The script that owns the RandR dance -- creating a mode before it can be
#: chosen, and clamping to the framebuffer Xvfb allocated at startup. Run by its
#: absolute path for the same reason `_AGENT_BROWSER` is: this process's PATH is
#: not an agent shell's.
_SET_DISPLAY_SIZE = "set-display-size"


#: The size the display starts at, and returns to when nobody is watching.
#:
#: Read from the image's own `WORKSPACE_XVFB_SCREEN` rather than repeated
#: here, because the image is what actually starts Xvfb at it. A default is
#: kept for a sandbox that predates the variable, and it matches the image's.
_FALLBACK_SCREEN = (1440, 960)


def default_display_size() -> tuple[int, int]:
    """What `WORKSPACE_XVFB_SCREEN` says, as width and height."""
    raw = os.environ.get("WORKSPACE_XVFB_SCREEN", "")
    parts = raw.lower().split("x")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        width, height = int(parts[0]), int(parts[1])
        if width > 0 and height > 0:
            return width, height
    return _FALLBACK_SCREEN


#: Brings up x11vnc and websockify. Not started with the display: they serve
#: a person watching, and measured at 67 MiB together in a sandbox where
#: most sessions have nobody watching at all.
_START_VNC_BRIDGE = "start-vnc-bridge"

#: Bounded well under the ten seconds a `websockets.connect` will wait for
#: a handshake, because this runs *before* `accept()`: a viewer whose relay
#: sits here longer than that gives up mid-handshake and sees a dropped
#: TCP connection with no close frame and no reason attached, which is
#: strictly worse than a refusal it can read.
#:
#: Shorter than the script's own worst case, and safe. The old worry was
#: killing the script between starting x11vnc and starting websockify, but
#: both launches happen in the first few hundred milliseconds and the rest
#: of the script is only waiting for ports; the children are `setsid`
#: detached, so killing the waiter never kills them, it only gives up
#: watching. `/vnc`'s own probe decides the answer either way, so giving up
#: early costs one refusal the viewer can read and retry, not a wrong yes.
#:
#: A backstop rather than a budget: measured cold, a whole first viewer --
#: bridge, handshake and first RFB frame -- takes 0.68 s, and a warm one
#: 0.33 s. Nothing reaches this number unless something is actually wrong.
_VNC_BRIDGE_TIMEOUT_SECONDS = 8.0


async def ensure_vnc_bridge() -> bool:
    """Start the viewing chain if it is not up. True when a viewer can be served.

    Called by `/vnc` before it accepts, because the relay is what serves the
    socket and so the relay is what must guarantee its own upstream. The
    backend's ensure string asks for this too, but not every caller comes
    through the backend -- the workspace e2e drives this route directly, and
    found exactly the gap this closes.

    Missing script means an older image, where both processes are already
    running because the display brought them up. Nothing to start, and not
    an error.

    **Stderr goes to a file and this waits on the process, rather than
    `communicate()`.** Measured, and the difference is not small: the same
    script takes 0.22s from a standalone `asyncio.run` and hits an
    eight-second timeout from inside the relay, every time, while its work
    has actually finished in the first fraction of a second. `communicate()`
    returns when the process exits *and* the pipe reaches EOF, and the write
    end of that pipe is inherited by the `setsid` grandchildren this script
    exists to leave running -- x11vnc and websockify outlive it deliberately,
    so the pipe never closes and the wait always runs to the timeout.

    In CI that timeout was the whole failure: the route sat here for longer
    than a `websockets.connect` will wait for a handshake, and the viewer saw
    a dropped TCP connection with no close frame and no reason. A file has no
    EOF to wait for, and it cannot fill and deadlock either.
    """
    bridge = sandbox_command(_START_VNC_BRIDGE)
    if not Path(bridge).exists():
        return True
    log = logging.getLogger(__name__)
    with tempfile.TemporaryDirectory(prefix="lemma-vnc-bridge-") as directory:
        errors = Path(directory) / "stderr"
        try:
            with errors.open("wb") as sink:
                process = await asyncio.create_subprocess_exec(
                    bridge,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=sink,
                )
        except OSError as exc:
            log.warning("could not start the VNC bridge: %r", exc)
            return False
        try:
            await asyncio.wait_for(process.wait(), timeout=_VNC_BRIDGE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(ProcessLookupError):
                await process.wait()
            log.warning(
                "the VNC bridge did not finish within %.0fs; anything it "
                "started is detached and left running",
                _VNC_BRIDGE_TIMEOUT_SECONDS,
            )
            return False
        if process.returncode != 0:
            # The whole of the script's stderr, not a 200-character prefix
            # of it: the script deliberately tails the failing process's
            # log into that stream, and truncating it throws away the only
            # evidence of why -- which is exactly what happened the last
            # time this failed.
            said = errors.read_bytes().decode("utf-8", "replace").strip()
            log.warning("the VNC bridge did not come up:\n%s", said)
            return False
    return True


class RecordingInProgress(RuntimeError):
    """A recording is running, so the display may not change size."""


async def recording_in_progress() -> bool:
    """Whether `agent-browser record` is capturing the display right now.

    Detected by the recorder process, because agent-browser has no `record
    status` to ask -- the CLI offers `start` and `stop` and nothing between
    them. Measured in the sandbox: zero `ffmpeg` processes before a take,
    exactly one during, zero after. Nothing else in this image runs ffmpeg
    on its own.

    `-x`, so the match is the program name and not a command line that
    happens to mention it -- the mistake the old memory guard made with its
    pattern list, which matched 1 of 14 Chromium processes.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "pgrep",
            "-x",
            "ffmpeg",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        # No `pgrep` is not evidence of a recording. Refusing every resize
        # on a missing tool would be worse than the thing this prevents.
        return False
    try:
        return await asyncio.wait_for(process.wait(), timeout=5) == 0
    except asyncio.TimeoutError:
        # Fail *closed*, unlike the missing-`pgrep` branch above. A probe
        # that hung is not evidence of no recording, and the two mistakes
        # do not cost the same: a wrong "yes" is a 409 the viewer can retry
        # a moment later, a wrong "no" resizes the framebuffer mid-take and
        # the recording is already spoiled by the time anyone sees it. The
        # missing-tool branch is the other way round because there the
        # answer never changes -- failing closed there refuses every resize
        # forever.
        with suppress(ProcessLookupError):
            process.kill()
        # Reaped, or the killed probe stays a zombie on a long-lived relay.
        with suppress(ProcessLookupError):
            await process.wait()
        return True


async def set_display_size(width: int, height: int) -> str | None:
    """Resize the shared display, returning the size it settled on.

    `None` when it could not be done -- an image without the script, or an X
    server that refused. The caller turns that into a refusal; the viewer
    keeps the display it already had, which is a worse fit rather than a
    broken one.

    One display serves every session in the sandbox, so this is deliberately
    not session-scoped: whoever asks last wins. That is the honest trade for
    now, and `app.py`'s `/vnc` docstring records per-session displays as the
    real fix.
    """
    resize = sandbox_command(_SET_DISPLAY_SIZE)
    if not Path(resize).exists():
        return None
    if await recording_in_progress():
        # The recorder is built around the framebuffer it started with --
        # its ffmpeg runs `-vf pad=...` sized at `record start` -- so moving
        # the display under it produces a broken take at best. A person
        # opening the pane, or the last one closing it, must not be able to
        # ruin a capture the agent is part-way through; the viewer keeps a
        # letterboxed picture instead, which is recoverable.
        raise RecordingInProgress(
            "the display is being recorded, so its size is held until the "
            "recording stops"
        )
    try:
        process = await asyncio.create_subprocess_exec(
            resize,
            str(width),
            str(height),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("could not resize the display: %r", exc)
        return None
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=_REAP_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        return None
    if process.returncode != 0:
        logging.getLogger(__name__).warning(
            "the display refused to resize: %s",
            stdout.decode("utf-8", "replace").strip()[:200],
        )
        return None
    return stdout.decode("utf-8", "replace").strip() or None


async def keepalive(*, session: str | None = None) -> bool:
    """Touch the browser so its idle timer does not retire it.

    `agent-browser` closes Chrome after five minutes without a *command*, and
    watching is not a command. So a person reading a page, or typing a password
    slowly, is idle by that measure and would have the browser shut under them.
    Any command resets the timer; asking for the URL is the cheapest one that
    does not change what is on screen.

    Returns whether the touch landed. A miss used to be silent, and a browser
    that then retired under a watcher looked like the page reloading itself
    every five minutes, with nothing anywhere saying why.

    Under `start_lock` for the same reason as `open_url`: with no daemon
    answering, `get url` spawns one, and it is one of the three callers that
    arrive together when a view opens.
    """
    async with start_lock(session):
        return await _touch(session)


async def _touch(session: str | None) -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            *agent_browser_argv("get", "url", session=session),
            env=agent_browser_env(session),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "browser keepalive for session %s did not run: %r", session, exc
        )
        return False
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_REAP_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        # Killed and reaped, or every later touch would add another stuck CLI.
        with suppress(ProcessLookupError):
            process.kill()
        await process.communicate()
        logging.getLogger(__name__).warning(
            "browser keepalive for session %s timed out after %ss",
            session,
            _REAP_TIMEOUT_SECONDS,
        )
        return False
    if process.returncode != 0:
        logging.getLogger(__name__).warning(
            "browser keepalive for session %s exited %s: %s",
            session,
            process.returncode,
            stderr.decode("utf-8", "replace").strip()[:200],
        )
        return False
    return True
