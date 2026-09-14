"""Structured browser tools over the ``agent-browser`` CLI in the sandbox.

The CLI is already installed and already the way the agent drives Chromium; what
it lacked was a typed surface. Everything browser-shaped went through
``exec_command``, so the model wrote its own shell quoting and the transcript had
nothing but a command string to render. These tools change the envelope, not the
transport.

Two deliberate choices:

**The page comes back with the action.** ``agent-browser`` hands out ``@eN`` refs
that go stale the moment the page changes, and acting on a stale ref is the
single most common failure. Every call that can change the page therefore
returns the snapshot taken *after* it, so the refs in a result are always the
ones to use next.

**Commands are chained in one shell round trip**, the way
``agent/tools/web/web_fetch.py`` already drives ``save-webpage``, with
``shlex.quote`` on every interpolated value. Argv is world-readable through
``/proc/<pid>/cmdline``, so nothing secret may travel this way — a stored
credential belongs in a file written over the runtime file API, the shape
``workspace_cli/github_credential_bridge.py`` uses.
"""

from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TypeAlias
from uuid import uuid4

from app.core.log.log import get_logger
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.tools.browser.script import (
    CHARS_PER_TOKEN,
    snapshot_argv_for,
    wait_steps,
    act_steps,
    build_script,
    classify_browser_failure,
    cli,
    head_truncate,
    new_sentinel,
    parse_script_output,
    read_steps,
    screenshot_argv,
    session_exports,
)
from app.modules.workspace.domain.browser_context import agent_session
from app.modules.agent.tools.browser.models import (
    BrowserActRequest,
    BrowserOpenRequest,
    BrowserReadRequest,
    BrowserResult,
    BrowserScreenshotRequest,
    BrowserScreenshotResponse,
    BrowserSnapshotRequest,
)
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.workspace.contracts.tooling import (
    retry_advice,
    sandbox_failure_types,
)
from app.modules.agent.tools.image_payload import downscale_for_vision
from app.modules.agent.tools.tool_errors import safe_described_error
from app.modules.agent.tools.workspace_cli.helper import (
    normalize_terminal_output,
    tail_truncate,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    get_workspace_session,
    workspace_runtime_context,
)
from app.modules.agent.tools.vision_delegation import describe_single_image
from app.modules.agent.tools.workspace_cli.models import ViewImageResponse
from pydantic_ai import BinaryContent, ToolReturn

logger = get_logger(__name__)

# The wait this call gives the browser. A page load plus a semantic wait fits
# comfortably; a browser that has to cold-start Xvfb and Chromium is the slow
# case, and `lemma-node-tool` does that on the first command of a session.
_BROWSER_TIMEOUT_SECONDS = 90

# Screenshots land under /tmp, never /workspace: /tmp is an allowed runtime root
# and dies with the sandbox, so a capture the agent looked at once does not
# accumulate in the user's files.
_SHOT_DIR = "/tmp/lemma-browser-shots"

# Enough headroom for a full-page screenshot to come back through the file API.
_SCREENSHOT_MAX_BYTES = 5 * 1024 * 1024


# Running the script is the one part of these tools that needs a sandbox, so it
# is a named collaborator rather than a private call: a test supplies its own and
# exercises the real script-building and parsing either side of it.
ScriptRunner: TypeAlias = Callable[
    [BaseAgentContext, str, str],
    Awaitable[tuple[str | None, BaseException | None]],
]

#: How a browser command reaches a shell. Injected so a test supplies a session
#: in front of this rather than replacing what is inside it.
SessionOpener: TypeAlias = Callable[[BaseAgentContext], Awaitable[object]]


async def _open_shell_session(ctx: BaseAgentContext):
    """The conversation's own shell, which is where the browser CLI lives."""
    runtime_context = workspace_runtime_context(ctx)
    return await get_workspace_session(
        ctx,
        session_id=runtime_context.default_shell_session_id,
        close_on_exit=False,
    )


async def run_browser_script(
    ctx: BaseAgentContext,
    script: str,
    operation: str,
    *,
    open_session: "SessionOpener | None" = None,
) -> tuple[str | None, BaseException | None]:
    """Run one chained script in the conversation's shell session.

    `open_session` is a seam so a test can hand this a session rather than
    reach into the module and replace the one it would have built: a double
    inside the subject certifies the half you did not write.
    """
    opener = open_session or _open_shell_session
    # The conversation's own browser, not the sandbox's shared one. A workspace
    # sandbox is per person and every agent that person runs shares it, so
    # without this a run inherits whatever cookies another conversation's agent
    # picked up -- including a login the person granted for a different task.
    script = f"{session_exports(agent_session(ctx.conversation_id))}{script}"
    try:
        with run_phase("tool.browser.session"):
            session = await opener(ctx)
        async with session:
            with run_phase("tool.browser.exec"):
                result = await session.exec_command(
                    cmd=script,
                    # The script's own output is bounded by the per-section
                    # truncation below; this ceiling only has to be large enough
                    # not to cut a snapshot off before we can trim it properly.
                    max_output_tokens=200_000,
                    timeout=_BROWSER_TIMEOUT_SECONDS,
                )
        stdout = result.get("stdout") or ""
        stderr = result.get("stderr") or ""
        output = normalize_terminal_output(f"{stdout}{stderr}")

        # The runtime's own verdict, not just its output. `exec_command` returns
        # `completed: False` when the wait window elapsed while the process ran
        # on, and `success: False` when it could not be run at all -- and in
        # both cases stdout is whatever had been printed so far, which for a
        # browser command is usually nothing. Reading only stdout is how a
        # timed-out `browser_snapshot` came back `success: true` with every
        # field null: true in the sense that nothing raised, and useless to the
        # agent reading it.
        timed_out = result.get("completed") is False
        refused = result.get("success") is False
        if timed_out or refused:
            # A command that timed out is still holding the browser daemon, and
            # the daemon serves every session in the sandbox -- so the next call
            # from this agent, and the relay behind the person watching, both
            # fail with "daemon may be busy" until something clears it. Nothing
            # did, so one slow page wedged the browser for the life of the
            # sandbox.
            await _restart_browser_daemon(ctx, operation=operation, opener=opener)
            reason = (
                f"the browser did not finish within {_BROWSER_TIMEOUT_SECONDS}s"
                if timed_out
                else str(result.get("error") or "the browser command could not run")
            )
            return None, BrowserCommandFailed(
                f"{reason}. The browser has been restarted, so trying again is "
                f"worth it -- but open the page again first, because the "
                f"restart took its tabs with it."
            )
        return output, None
    except sandbox_failure_types() as exc:
        # Only the sandbox's own failures are shaped into a tool result, because
        # only those have an answer for "is this worth retrying". Anything else
        # is a bug, and belongs at the GracefulToolset boundary where it is
        # logged as one rather than reported to the model as a browser problem.
        logger.debug(
            "agent.browser.script_failed.diagnostic",
            operation=operation,
            exc_info=exc,
        )
        return None, exc


class BrowserCommandFailed(RuntimeError):
    """A browser command that did not run, or did not finish."""


async def _restart_browser_daemon(
    ctx: BaseAgentContext,
    *,
    operation: str,
    opener: "SessionOpener | None" = None,
) -> None:
    """Clear a wedged browser daemon so the next command can work.

    `agent-browser` runs one daemon per sandbox and every command talks to it,
    so a single hung command makes every later one fail -- the agent's, and the
    relay's behind a person watching the same browser. Killing it is the
    recovery: the next invocation starts a fresh one.

    Best effort by design. This runs while something has already gone wrong,
    and a failure to clean up must not replace the error the caller is about to
    report with a less useful one.
    """
    logger.warning("agent.browser.daemon_restarted.degraded", operation=operation)
    with suppress(Exception):
        session = await (opener or _open_shell_session)(ctx)
        async with session:
            await session.exec_command(
                cmd="pkill -f agent-browser || true",
                max_output_tokens=256,
                timeout=20,
            )


def _failure(exc: BaseException, *, operation: str) -> BrowserResult:
    return BrowserResult(
        success=False,
        error=(
            f"Browser {operation} failed before the tool could complete: "
            f"{safe_described_error(exc)}." + retry_advice(exc)
        ),
    )


async def _act_and_report(
    ctx: BaseAgentContext,
    *,
    action_steps: list[list[str]],
    operation: str,
    snapshot_argv: list[str] | None,
    max_snapshot_tokens: int,
    run_script: ScriptRunner,
) -> BrowserResult:
    sentinel = new_sentinel()
    script = build_script(
        [cli(step) for step in action_steps],
        sentinel=sentinel,
        snapshot_argv=snapshot_argv,
    )
    output, exc = await run_script(ctx, script, operation)
    if exc is not None:
        return _failure(exc, operation=operation)

    parsed = parse_script_output(output, sentinel=sentinel)
    shed = classify_browser_failure(
        return_code=parsed.return_code, output=parsed.action
    )
    snapshot, truncated = head_truncate(parsed.snapshot, max_tokens=max_snapshot_tokens)
    failed = shed is not None or (parsed.return_code not in (0, None))
    return BrowserResult(
        success=not failed,
        error=(
            shed
            if shed
            else (f"The browser command failed: {parsed.action}" if failed else None)
        ),
        url=parsed.url,
        title=parsed.title,
        snapshot=snapshot,
        output=parsed.action or None,
        truncated=truncated,
    )


async def open_internal(
    ctx: BaseAgentContext,
    request: BrowserOpenRequest,
    *,
    run_script: ScriptRunner = run_browser_script,
) -> BrowserResult:
    steps = [["open", request.url]]
    steps.extend(
        wait_steps(
            wait_for_url=request.wait_for_url, wait_for_text=request.wait_for_text
        )
    )
    return await _act_and_report(
        ctx,
        action_steps=steps,
        operation="open",
        snapshot_argv=snapshot_argv_for(interactive_only=True),
        max_snapshot_tokens=request.max_snapshot_tokens,
        run_script=run_script,
    )


async def snapshot_internal(
    ctx: BaseAgentContext,
    request: BrowserSnapshotRequest,
    *,
    run_script: ScriptRunner = run_browser_script,
) -> BrowserResult:
    return await _act_and_report(
        ctx,
        action_steps=[],
        operation="snapshot",
        snapshot_argv=snapshot_argv_for(interactive_only=request.interactive_only),
        max_snapshot_tokens=request.max_snapshot_tokens,
        run_script=run_script,
    )


async def act_internal(
    ctx: BaseAgentContext,
    request: BrowserActRequest,
    *,
    run_script: ScriptRunner = run_browser_script,
) -> BrowserResult:
    steps = act_steps(request)
    if isinstance(steps, str):
        return BrowserResult(success=False, error=steps)
    steps = [*steps]
    steps.extend(
        wait_steps(
            wait_for_url=request.wait_for_url, wait_for_text=request.wait_for_text
        )
    )
    return await _act_and_report(
        ctx,
        action_steps=steps,
        operation=f"act:{request.action}",
        snapshot_argv=snapshot_argv_for(interactive_only=True),
        max_snapshot_tokens=request.max_snapshot_tokens,
        run_script=run_script,
    )


async def read_internal(
    ctx: BaseAgentContext,
    request: BrowserReadRequest,
    *,
    run_script: ScriptRunner = run_browser_script,
) -> BrowserResult:
    steps = read_steps(request)
    if isinstance(steps, str):
        return BrowserResult(success=False, error=steps)

    sentinel = new_sentinel()
    # A read never changes the page, so it does not pay for a snapshot.
    script = build_script(
        [cli(step) for step in steps], sentinel=sentinel, snapshot_argv=None
    )
    output, exc = await run_script(ctx, script, f"read:{request.what}")
    if exc is not None:
        return _failure(exc, operation=f"read:{request.what}")

    parsed = parse_script_output(output, sentinel=sentinel)
    shed = classify_browser_failure(
        return_code=parsed.return_code, output=parsed.action
    )
    failed = shed is not None or (parsed.return_code not in (0, None))
    # Console and network logs are a tail: the newest entries are the ones that
    # explain what just happened.
    limit = request.max_output_tokens * CHARS_PER_TOKEN
    body = (
        tail_truncate(parsed.action, limit)
        if request.what in {"console", "network"}
        else head_truncate(parsed.action, max_tokens=request.max_output_tokens)[0]
    )
    return BrowserResult(
        success=not failed,
        error=shed
        if shed
        else (f"The browser read failed: {parsed.action}" if failed else None),
        url=parsed.url,
        title=parsed.title,
        output=body,
        truncated=bool(parsed.action and len(parsed.action) > limit),
    )


async def screenshot_internal(
    ctx: BaseAgentContext, request: BrowserScreenshotRequest
) -> BrowserScreenshotResponse | ViewImageResponse | ToolReturn:
    """Capture the page and hand it back as binary tool content.

    The image travels over the runtime file API rather than through the shell:
    base64 on stdout would be bounded by the command's output ceiling, and a
    full-page capture is exactly the case that exceeds it.
    """
    suffix = "png" if request.annotate else "jpeg"
    path = f"{_SHOT_DIR}/{uuid4().hex}.{suffix}"
    sentinel = new_sentinel()
    script = build_script(
        [
            f"mkdir -p {shlex.quote(_SHOT_DIR)}",
            cli(screenshot_argv(request, path=path)),
        ],
        sentinel=sentinel,
        snapshot_argv=None,
    )

    try:
        runtime_context = workspace_runtime_context(ctx)
        with run_phase("tool.browser.session"):
            session = await get_workspace_session(
                ctx,
                session_id=runtime_context.default_shell_session_id,
                close_on_exit=False,
            )
        async with session:
            with run_phase("tool.browser.exec"):
                result = await session.exec_command(
                    cmd=script,
                    max_output_tokens=20_000,
                    timeout=_BROWSER_TIMEOUT_SECONDS,
                )
            merged = normalize_terminal_output(
                f"{result.get('stdout') or ''}{result.get('stderr') or ''}"
            )
            parsed = parse_script_output(merged, sentinel=sentinel)
            shed = classify_browser_failure(
                return_code=parsed.return_code, output=parsed.action
            )
            if shed is not None:
                return BrowserScreenshotResponse(success=False, error=shed)
            if parsed.return_code not in (0, None):
                return BrowserScreenshotResponse(
                    success=False,
                    error=f"The screenshot command failed: {parsed.action}",
                    url=parsed.url,
                    title=parsed.title,
                )
            content = await session.read_file(path)
            try:
                await session.delete_file(path)
            except sandbox_failure_types():
                # A stray capture in /tmp dies with the sandbox. Failing the
                # tool over the cleanup would throw away the image we came for.
                logger.debug(
                    "agent.browser.screenshot_cleanup_failed.diagnostic",
                    exc_info=True,
                )
    except sandbox_failure_types() as exc:
        logger.debug("agent.browser.screenshot_failed.diagnostic", exc_info=exc)
        return BrowserScreenshotResponse(
            success=False,
            error=(
                "Browser screenshot failed before the tool could complete: "
                f"{safe_described_error(exc)}." + retry_advice(exc)
            ),
        )

    media_type = "image/png" if request.annotate else "image/jpeg"
    payload, payload_media_type = downscale_for_vision(content, media_type)
    if len(payload) > _SCREENSHOT_MAX_BYTES:
        return BrowserScreenshotResponse(
            success=False,
            error=(
                f"The screenshot is {len(payload) // 1024} KB even after "
                f"downscaling, over the {_SCREENSHOT_MAX_BYTES // (1024 * 1024)} MB "
                "limit. Capture the viewport instead of the full page, or narrow "
                "the part of the page you need."
            ),
            url=parsed.url,
            title=parsed.title,
            size_bytes=len(content),
            full_page=request.full_page,
        )

    if getattr(ctx, "vision_mode", AgentVisionMode.UNAVAILABLE) is not (
        AgentVisionMode.DIRECT
    ):
        return await describe_single_image(
            ctx,
            data=payload,
            media_type=payload_media_type,
            file_path=parsed.url or path,
            source="workspace",
            instructions=request.instructions,
        )

    return ToolReturn(
        return_value=BrowserScreenshotResponse(
            success=True,
            message=f"Screenshot of {parsed.url or 'the current page'}.",
            url=parsed.url,
            title=parsed.title,
            media_type=media_type,
            size_bytes=len(content),
            full_page=request.full_page,
        ),
        content=[BinaryContent(data=payload, media_type=payload_media_type)],
    )
