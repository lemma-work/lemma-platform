"""Turning a finished sandbox's stdout/stderr into what we store.

One implementation, because there were two and they drifted. A function run
reports its terminal state through either the dispatcher (the synchronous path)
or the runtime gateway (the sandbox's own callback), and each grew its own copy
of this. When the redact-then-truncate order was fixed it was fixed in one of
them, so the public callback surface — the one whose payload size is decided by
the sandbox rather than by us — kept the slow version.
"""

from __future__ import annotations

from app.core.redaction import redact_text
from app.modules.function.contracts.runtime import RuntimeTerminalRequest

#: What we keep. Anything past this is discarded before it reaches the database.
LOG_LIMIT_BYTES = 4 * 1024 * 1024
#: Extra text redacted beyond the limit, so a credential straddling the final
#: boundary is still inside the window the patterns ran over.
REDACTION_MARGIN_BYTES = 64 * 1024


def terminal_logs(request: RuntimeTerminalRequest) -> str | None:
    """Redact what we are keeping, not what we are about to throw away.

    This used to redact the whole of stdout+stderr — up to 8 MiB — with thirteen
    regex passes, and then keep the first 4 MiB. Half the work was spent on text
    nobody would ever see, and on the event loop.

    Cutting first is safe as long as the cut is not where a secret is, which is
    what the margin above is for.
    """
    sections: list[str] = []
    if request.stdout:
        sections.append(request.stdout)
    if request.stderr:
        sections.append(request.stderr)
    if request.output_truncated:
        sections.append("[function output truncated]")
    if not sections:
        return None
    combined = "\n".join(sections)
    return redact_text(combined[: LOG_LIMIT_BYTES + REDACTION_MARGIN_BYTES])[
        :LOG_LIMIT_BYTES
    ]
