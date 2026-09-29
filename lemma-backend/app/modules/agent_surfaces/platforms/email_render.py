"""Writing an outbound email: agent content to the parts a mail client shows.

The mirror of :mod:`email_text`. Content arrives as text, markdown or HTML and
has to leave as a plain-text part and an HTML part, because a mail client picks
whichever it can display and a reader may see either one.
"""

from __future__ import annotations

import re
from typing import Literal

from app.modules.agent_surfaces.platforms.email_styles import (
    EMAIL_MARKDOWN_EXTENSIONS,
    EmailStylesExtension,
    email_body_wrapper,
    style_stashed_code_blocks,
)
from app.modules.agent_surfaces.platforms.email_text import plain_text_from_html

try:
    import markdown as markdown_lib
except ImportError:  # pragma: no cover - optional dependency fallback
    markdown_lib = None

EmailReplyContentType = Literal["text", "markdown", "html"]


def render_email_content(
    *,
    content: str,
    content_type: EmailReplyContentType,
) -> tuple[str, str | None]:
    normalized_content = str(content or "").strip()
    if content_type == "text":
        return normalized_content, None
    if content_type == "html":
        return plain_text_from_html(normalized_content), normalized_content
    if markdown_lib is None:
        escaped = (
            normalized_content.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return normalized_content, email_body_wrapper(f"<pre>{escaped}</pre>")
    return normalized_content, email_body_wrapper(
        _markdown_to_email_html(normalized_content)
    )


_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\S")


def _blank_line_before_lists(content: str) -> str:
    """Let a list interrupt a paragraph, the way CommonMark already would.

    Python-Markdown requires a blank line before a list. Without one, a label
    line followed straight by bullets is one paragraph, and because HTML does
    not preserve newlines the whole thing arrives as a single flowed sentence
    with hyphens loose in it -- "What you can do - Land results - Run agents".

    That shape is close to the modal form of model output, so it is worth
    meeting rather than correcting: no docstring reliably stops a model writing
    a list directly under its heading, and every agent already deployed writes
    it that way today.

    Only the email path needs this. A chat surface renders newlines as
    newlines, so the same message already reads as a list there.

    ``in_list`` is what keeps a lazy continuation safe. Inside a list an
    unindented line belongs to the item above it, so a break inserted before
    the next item would split one list in two:

        - item one
          continued on the next line
        - item two
    """
    out: list[str] = []
    in_list = False
    in_fence = False
    fence = ""
    for line in content.split("\n"):
        stripped = line.strip()
        if in_fence:
            if stripped.startswith(fence):
                in_fence = False
            out.append(line)
            continue
        # A hyphen inside a fenced block is code, not a bullet.
        if stripped.startswith(("```", "~~~")):
            in_fence, fence = True, stripped[:3]
            out.append(line)
            continue
        if not stripped:
            in_list = False
            out.append(line)
            continue
        if _LIST_ITEM.match(line):
            if not in_list and out and out[-1].strip():
                out.append("")
            in_list = True
        out.append(line)
    return "\n".join(out)


def _markdown_to_email_html(content: str) -> str:
    """Render markdown with the extensions agents actually write against.

    A fresh `Markdown` instance per call rather than a module-level one: the
    parser carries per-document state (footnote refs, link definitions) and
    resetting it is easy to forget. Rendering an email is not a hot path.
    """
    return style_stashed_code_blocks(
        markdown_lib.markdown(
            _blank_line_before_lists(content),
            extensions=[*EMAIL_MARKDOWN_EXTENSIONS, EmailStylesExtension()],
        )
    )
