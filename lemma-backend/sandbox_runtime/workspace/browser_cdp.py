"""Reaching Chrome's debugging protocol from outside the sandbox.

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
minutes, and ``browser_guard`` SIGKILLs it under memory pressure. So the file is
a record of where Chrome *was*, and reading it alone reports a port that nothing
is listening on. It cost a long debugging session: a viewer that asked to watch
a browser which had timed out got a connection error rather than "not running",
which surfaced as a 500 and, to the person clicking, as an unexplained failure.
Hence: the recorded port is a candidate, and it is not believed until something
answers on it.

**The port is not reachable.** Only the runtime's own port is published, so CDP
is only ever reached *through* this runtime -- which is also the right answer for
safety, because it puts a place to stand between a browser tab and full control
of the session.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

#: Chrome writes the port here on launch; the second line is the browser's own
#: WebSocket path, which is not what a page-level client wants.
_ACTIVE_PORT_FILE = Path("/tmp/lemma-browser/profile/DevToolsActivePort")

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

#: Spelled absolutely, because **the runtime's PATH is not the agent's PATH**.
#:
#: Two things answer to `agent-browser` in this image: the raw npm binary in
#: `/opt/lemma-node/node_modules/.bin`, and the `lemma-node-tool` wrapper in
#: `/usr/local/bin` which runs `start-browser` first -- writing the config file
#: and starting Xvfb -- before handing over. An agent shell finds the wrapper.
#: This process does not: `/opt/lemma-node/node_modules/.bin` comes earlier on
#: its PATH, so the bare name resolves to the binary that cannot bootstrap, and
#: in a container where nothing has used the browser yet it fails with
#: `config file not found` -- which reads like a broken image rather than a
#: missing prerequisite. Naming the wrapper is what makes a cold sandbox work.
_AGENT_BROWSER = "/usr/local/bin/agent-browser"

#: Long enough to distinguish "refused" from "busy", short enough that a viewer
#: is not left waiting on a browser that has gone.
_PROBE_TIMEOUT_SECONDS = 2.0

#: How long to let the CLI exit on its own once it has answered, before killing
#: it. It has already given us the port by this point, so nobody is waiting on
#: the difference.
_REAP_TIMEOUT_SECONDS = 5.0


class BrowserNotRunning(RuntimeError):
    """Chrome is not up, so there is nothing to attach to."""


def recorded_port() -> int:
    """The port Chrome last recorded, which it may well have left behind.

    Never use this without probing it -- see the module docstring. It is public
    only because "what does the file claim" is worth being able to ask.
    """
    try:
        first_line = _ACTIVE_PORT_FILE.read_text().splitlines()[0].strip()
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
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def live_port() -> int:
    """Where Chrome is listening *now*, without starting it.

    This is the ambient answer: a workspace whose browser has been shed for
    idleness or memory is the ordinary resting state, and asking to look at it
    should not conjure one.
    """
    port = recorded_port()
    if not await _answers_on(port):
        raise BrowserNotRunning("the browser is not running")
    return port


async def ensure_port() -> int:
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
            _AGENT_BROWSER if Path(_AGENT_BROWSER).exists() else "agent-browser",
            "get",
            "cdp-url",
            stdout=asyncio.subprocess.PIPE,
            # Merged rather than a second pipe: one stream cannot deadlock
            # against the other filling its buffer, and when a start fails the
            # explanation and the output arrive in the order they happened.
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        logger.warning("could not run the browser CLI: %r", exc)
        raise BrowserNotRunning("the browser could not be started") from exc

    try:
        return await asyncio.wait_for(
            _read_port(process), timeout=_START_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        logger.warning("the browser did not start within %ss", _START_TIMEOUT_SECONDS)
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
    logger.warning(
        "the browser did not start: %s", " | ".join(transcript) or "no output"
    )
    raise BrowserNotRunning("the browser could not be started")


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
