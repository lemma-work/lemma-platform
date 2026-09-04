"""Building the shell command, and reading its output back.

Split from `browser.py` because none of it needs a sandbox: the shape of the
command, the sections its output arrives in, and what a failure means are all
decidable on their own, and are tested that way.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from uuid import uuid4

from app.modules.agent.tools.browser.models import (
    BrowserActRequest,
    BrowserReadRequest,
    BrowserScreenshotRequest,
)


# Roughly what the rest of the tools assume when converting a token budget into
# bytes (see `sandbox_session.exec_command`'s output limit).
CHARS_PER_TOKEN = 4


def new_sentinel() -> str:
    """A per-call output delimiter that page content cannot forge.

    The sections of a chained script are split on this string. A fixed marker
    would be something a hostile page could print into the console or a title
    and so move the boundary between "what the browser said" and "what the page
    said"; a fresh random one per call cannot be predicted.
    """
    return f"__LEMMA_BROWSER_SPLIT_{uuid4().hex}__"


def cli(argv: list[str]) -> str:
    return " ".join(["agent-browser", *(shlex.quote(part) for part in argv)])


def snapshot_argv_for(*, interactive_only: bool) -> list[str]:
    """`agent-browser snapshot`, machine-readable.

    No link-URL flag: 0.32.3 has `-i` and `--json` and nothing that adds hrefs,
    so offering the option would promise the agent something the CLI cannot do.
    `browser_read(what="attr", attribute="href")` is how you get one.
    """
    argv = ["snapshot", "--json"]
    if interactive_only:
        argv.append("-i")
    return argv


def wait_steps(
    *, wait_for_url: str | None, wait_for_text: str | None
) -> list[list[str]]:
    steps: list[list[str]] = []
    if wait_for_url:
        steps.append(["wait", "--url", wait_for_url])
    if wait_for_text:
        steps.append(["wait", "--text", wait_for_text])
    return steps


def build_script(
    action_commands: list[str],
    *,
    sentinel: str,
    snapshot_argv: list[str] | None,
) -> str:
    """One shell command: run the action, then report where the page ended up.

    Takes ready shell commands rather than argv, because not every step is an
    ``agent-browser`` call — a capture has to create its output directory first.
    Build the CLI ones with :func:`cli`.

    The action commands are chained with ``&&`` so a failure stops the sequence,
    but the reporting tail runs unconditionally — where the page actually is
    after a failed click is exactly what the agent needs to recover, and a
    chain that abandoned it would report only that something went wrong.
    """
    marker = f"printf '\\n%s\\n' {shlex.quote(sentinel)}"
    action = " && ".join(action_commands) or "true"
    parts = [
        f"{{ {action} ; }} 2>&1",
        "__lemma_rc=$?",
        marker,
        'printf "rc=%s\\n" "$__lemma_rc"',
        marker,
        f"{cli(['get', 'url'])} 2>/dev/null",
        marker,
        f"{cli(['get', 'title'])} 2>/dev/null",
    ]
    if snapshot_argv is not None:
        parts.extend([marker, f"{cli(snapshot_argv)} 2>/dev/null"])
    return " ; ".join(parts)


@dataclass(frozen=True, slots=True)
class ScriptOutput:
    """The sections of a chained script's stdout, already split apart."""

    action: str
    return_code: int | None
    url: str | None
    title: str | None
    snapshot: str | None


def parse_script_output(stdout: str | None, *, sentinel: str) -> ScriptOutput:
    """Split a chained script's output back into its parts.

    Missing trailing sections are normal rather than an error: a shell that died
    partway through still produced whatever ran before it, and that is more
    useful to report than a parse failure.
    """
    sections = (stdout or "").split(sentinel)
    parts = [section.strip() for section in sections]

    def at(index: int) -> str | None:
        if index >= len(parts):
            return None
        return parts[index] or None

    return_code: int | None = None
    raw_rc = at(1)
    if raw_rc and raw_rc.startswith("rc="):
        try:
            return_code = int(raw_rc.removeprefix("rc=").strip())
        except ValueError:
            return_code = None

    return ScriptOutput(
        action=parts[0] if parts else "",
        return_code=return_code,
        url=at(2),
        title=at(3),
        snapshot=at(4),
    )


# Exit codes and CLI messages that mean the browser itself is gone rather than
# the page misbehaving. 137 is SIGKILL, which is what `browser_guard` sends when
# available memory drops below its floor.
_BROWSER_GONE_EXIT_CODES = frozenset({124, 137})
_BROWSER_GONE_MARKERS = (
    "econnrefused",
    "target closed",
    "target page, context or browser has been closed",
    "browser has been closed",
    "websocket error",
    "connect econnrefused",
    "failed to connect to the browser",
    "no browser session",
)

BROWSER_SHED_ADVICE = (
    "The browser is not running. It is shed automatically when the sandbox runs "
    "low on memory, and closes itself after two minutes idle, so this is normal "
    "rather than a fault. The next browser call starts it again — retry once, "
    "and reopen the page you were on, because tabs and any login in the profile "
    "did not survive."
)


def classify_browser_failure(*, return_code: int | None, output: str) -> str | None:
    """Say why the browser call failed, when the reason is the browser itself.

    Without this the agent sees a bare non-zero exit and no explanation, and the
    memory shed in particular is invisible — which is the failure
    `sandbox_runtime/workspace/browser_guard.py` was written from.
    """
    if return_code in _BROWSER_GONE_EXIT_CODES:
        return BROWSER_SHED_ADVICE
    lowered = (output or "").lower()
    if any(marker in lowered for marker in _BROWSER_GONE_MARKERS):
        return BROWSER_SHED_ADVICE
    return None


def head_truncate(text: str | None, *, max_tokens: int) -> tuple[str | None, bool]:
    """Keep the beginning, which for a page is the part that matters.

    The opposite of `tail_truncate`, and for the opposite reason: a terminal's
    live state is its last line, while a snapshot's most useful elements are the
    ones nearest the top of the document.
    """
    if text is None:
        return None, False
    limit = max_tokens * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n…[snapshot truncated — narrow the page or scroll]…", True


def act_steps(request: BrowserActRequest) -> list[list[str]] | str:
    """Argv for one action, or the reason the request cannot be carried out.

    Returned rather than raised: an argument mistake has to reach the model as a
    structured `success: false` it can correct, not as a validation error that
    burns its retry budget — the rule `workspace_cli/models.py` states for
    `view_image`.
    """
    action = request.action
    target = (request.target or "").strip()
    text = request.text
    needs_target = action in {
        "click",
        "fill",
        "type",
        "select",
        "check",
        "uncheck",
        "hover",
    }
    if needs_target and not target:
        return (
            f"`target` is required for action='{action}' — pass an @eN ref from "
            "the latest snapshot."
        )
    if action in {"fill", "type", "select"} and text is None:
        return f"`text` is required for action='{action}'."
    if action == "press" and not (request.key or "").strip():
        return "`key` is required for action='press', e.g. 'Enter'."

    if action == "press":
        return [["press", request.key or ""]]
    if action == "scroll":
        return [["scroll", request.scroll_direction, str(request.scroll_amount)]]
    if action in {"fill", "type", "select"}:
        return [[action, target, text or ""]]
    return [[action, target]]


def read_steps(request: BrowserReadRequest) -> list[list[str]] | str:
    """Argv for one read, or the reason it cannot be carried out."""
    what = request.what
    target = (request.target or "").strip()
    if what in {"text", "attr"} and not target:
        return f"`target` is required for what='{what}' — pass an @eN ref."
    if what == "attr" and not (request.attribute or "").strip():
        return "`attribute` is required for what='attr', e.g. 'href'."

    if what == "url":
        return [["get", "url"]]
    if what == "title":
        return [["get", "title"]]
    if what == "console":
        return [["console"]]
    if what == "network":
        # `network` alone is a usage error: the CLI wants an action, and
        # `requests` is the one a reader means by "the network log".
        return [["network", "requests"]]
    if what == "attr":
        return [["get", "attr", target, request.attribute or ""]]
    if what == "html":
        return [["get", "html", target or "html"]]
    return [["get", "text", target]]


def screenshot_argv(request: BrowserScreenshotRequest, *, path: str) -> list[str]:
    """Argv for one capture.

    JPEG for an ordinary shot, matching `sandbox-images/scripts/save-webpage.sh`;
    PNG when annotating, because the numbered labels are thin high-contrast
    graphics and JPEG rings around exactly that.
    """
    argv = ["screenshot"]
    if request.full_page:
        argv.append("--full")
    if request.annotate:
        argv.append("--annotate")
    else:
        argv.extend(["--screenshot-format", "jpeg", "--screenshot-quality", "85"])
    argv.append(path)
    return argv
