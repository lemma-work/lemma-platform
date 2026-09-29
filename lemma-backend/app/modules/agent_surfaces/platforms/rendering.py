"""Pure outbound text rendering + chunking for agent surfaces.

These are I/O-free helpers (trivially unit-testable). Currently used by the
Telegram service to render assistant Markdown into Telegram MarkdownV2 and to
split long replies under the per-message length limit. Other platforms keep
their existing renderers; the chunker is generic and can be reused later.

The MarkdownV2 converter is intentionally conservative: it converts the common
constructs LLMs emit (bold, italic, strikethrough, inline code, fenced code,
links) and escapes everything else. Anything it gets wrong is caught by the
caller's plain-text fallback (a 400 ``can't parse entities`` retried without a
parse mode), so it can never hard-fail a delivery.
"""

from __future__ import annotations

import re

from app.core.text.thinking_tags import (
    ThinkingStreamSplitter,
    strip_thinking_tokens as core_strip_thinking_tokens,
)

# Characters MarkdownV2 reserves in normal text; each must be backslash-escaped.
_MD_V2_RESERVED = set(r"_*[]()~`>#+-=|{}.!\\")
# Ordered alternation: fenced code and inline code first (so their contents are
# protected), then bold/strikethrough before italic (so ``**`` is not mistaken
# for two italics), then links.
_TOKEN_RE = re.compile(
    r"```[\s\S]*?```"  # fenced code block
    r"|`[^`\n]+`"  # inline code
    r"|\*\*[\s\S]+?\*\*"  # **bold**
    r"|__[\s\S]+?__"  # __bold__
    r"|~~[\s\S]+?~~"  # ~~strikethrough~~
    r"|\*[^*\n]+?\*"  # *italic*
    r"|_[^_\n]+?_"  # _italic_
    r"|\[[^\]\n]+\]\([^)\n]+\)"  # [text](url)
)

_FENCE_RE = re.compile(r"^```([^\n`]*)\n?([\s\S]*?)```$")


def escape_markdown_v2(text: str) -> str:
    """Escape every MarkdownV2 reserved character in plain text."""
    return "".join("\\" + ch if ch in _MD_V2_RESERVED else ch for ch in text)


def _escape_code(text: str) -> str:
    """Inside code/pre entities only backslash and backtick are escaped."""
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _escape_url(text: str) -> str:
    """Inside a link target only backslash and the closing paren are escaped."""
    return text.replace("\\", "\\\\").replace(")", "\\)")


def _convert_token(token: str) -> str:
    if token.startswith("```"):
        match = _FENCE_RE.match(token)
        if match is None:
            return escape_markdown_v2(token)
        language, body = match.group(1).strip(), match.group(2)
        return f"```{language}\n{_escape_code(body)}```"
    if token.startswith("`"):
        return f"`{_escape_code(token[1:-1])}`"
    if token.startswith("**") and token.endswith("**"):
        return f"*{escape_markdown_v2(token[2:-2])}*"
    if token.startswith("__") and token.endswith("__"):
        return f"*{escape_markdown_v2(token[2:-2])}*"
    if token.startswith("~~") and token.endswith("~~"):
        return f"~{escape_markdown_v2(token[2:-2])}~"
    if token.startswith("*") and token.endswith("*"):
        return f"_{escape_markdown_v2(token[1:-1])}_"
    if token.startswith("_") and token.endswith("_"):
        return f"_{escape_markdown_v2(token[1:-1])}_"
    if token.startswith("["):
        text, url = token[1:].split("](", 1)
        url = url[:-1]
        return f"[{escape_markdown_v2(text)}]({_escape_url(url)})"
    return escape_markdown_v2(token)


def to_markdown_v2(text: str) -> str:
    """Convert assistant Markdown to Telegram MarkdownV2.

    Recognized formatting is converted to its MarkdownV2 equivalent; all other
    text has its reserved characters escaped so the result parses cleanly.
    """
    out: list[str] = []
    pos = 0
    for match in _TOKEN_RE.finditer(text):
        if match.start() > pos:
            out.append(escape_markdown_v2(text[pos : match.start()]))
        out.append(_convert_token(match.group()))
        pos = match.end()
    if pos < len(text):
        out.append(escape_markdown_v2(text[pos:]))
    return "".join(out)


def _safe_cut(chunk: str) -> str:
    """Trim a trailing unpaired backslash so a hard split never severs an
    escape sequence (which would break MarkdownV2 parsing)."""
    trailing = len(chunk) - len(chunk.rstrip("\\"))
    if trailing % 2 == 1:
        return chunk[:-1]
    return chunk


# Room kept in every chunk of a text that has code fences, for the closing fence
# a chunk may need appended. The reopening fence goes on the next chunk, which
# is why the split runs under `limit - _FENCE_RESERVE` rather than at `limit`.
_FENCE_RESERVE = 16


def chunk_text(text: str, *, limit: int) -> list[str]:
    """Split ``text`` into chunks no longer than ``limit`` characters.

    Prefers paragraph (``\\n\\n``) then line (``\\n``) then word boundaries;
    hard-splits only a single run longer than ``limit``. Safe to call on
    already-rendered MarkdownV2 because it never cuts an escape pair.

    Code fences are kept balanced across the split: a chunk that ends inside a
    fence is closed, and the next one reopens it with the same language tag.
    Cut mid-fence, both halves render wrong -- the first swallows the rest of
    the message into a code block, and the second shows the closing marker as
    literal text.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return [text] if text else []
    if "```" not in text or limit <= 4 * _FENCE_RESERVE:
        return _split_text(text, limit)

    chunks: list[str] = []
    reopen = ""
    for chunk in _split_text(text, limit - _FENCE_RESERVE):
        if reopen:
            first_line, _, rest = chunk.partition("\n")
            if first_line.strip() == "```":
                # The fence this chunk would have reopened is closing right
                # here, and the previous chunk already closed it for us.
                chunk = rest.lstrip("\n")
                if not chunk:
                    reopen = ""
                    continue
            else:
                chunk = f"{reopen}\n{chunk}"
        language = _fence_left_open(chunk)
        if language is None:
            reopen = ""
        else:
            chunk = f"{chunk}\n```"
            reopen = f"```{language}"
        chunks.append(chunk)
    return chunks


def _fence_left_open(chunk: str) -> str | None:
    """The language tag of the fence this chunk ends inside, or ``None``.

    A line that opens and closes a fence on its own (```code```) does not count.
    """
    language: str | None = None
    for line in chunk.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("```"):
            continue
        if language is not None:
            language = None
        elif "```" not in stripped[3:]:
            language = stripped[3:].strip()
    return language


def _split_text(text: str, limit: int) -> list[str]:
    """The boundary-preferring split behind :func:`chunk_text`."""
    if len(text) <= limit:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = -1
        for separator in ("\n\n", "\n", " "):
            candidate = window.rfind(separator)
            if candidate > 0:
                split_at = candidate + (len(separator) if separator != " " else 0)
                break
        if split_at <= 0:
            head = _safe_cut(window)
            split_at = len(head) if head else limit
        piece = remaining[:split_at].rstrip()
        if piece:
            chunks.append(piece)
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


# --- Thinking / reasoning token stripping ------------------------------------
#
# The convention itself -- which tags, how a straddled tag is handled, what an
# unclosed block means -- lives in `app/core/text/thinking_tags`, because the
# agent module needs the same answers and a module may not import another's
# infrastructure. What is left here is the surfaces *policy*: reasoning must
# never reach Slack or Telegram at all, so the split is taken and the reasoning
# half thrown away. The agent module takes the same split and keeps both halves.


def strip_thinking_tokens(text: str) -> str:
    """Remove reasoning blocks from model output. See `core.text.thinking_tags`."""
    return core_strip_thinking_tokens(text)


def sanitize_user_visible_text(text: str | None) -> str:
    """The single boundary every model-authored string passes through before it
    leaves the backend for a surface.

    Strips reasoning so it can never reach a user on any path -- progress
    updates, questions, approvals, captions, final answers. Safe on
    ``None``/empty input.
    """
    return core_strip_thinking_tokens(text or "")


class ThinkingStreamFilter:
    """Drop reasoning from a *token stream*.

    ``strip_thinking_tokens`` works on a whole message. Streaming has neither
    luxury: a tag can straddle two deltas, so a naive per-delta strip lets
    ``<thi`` + ``nk>`` through and the user reads the model's reasoning.

    A thin policy over `ThinkingStreamSplitter`: the splitter decides what is
    reasoning, this decides that reasoning is not for surfaces.
    """

    def __init__(self) -> None:
        self._splitter = ThinkingStreamSplitter()

    def feed(self, delta: str) -> str:
        """Return the part of ``delta`` that is safe to show now."""
        return "".join(
            chunk for kind, chunk in self._splitter.feed(delta) if kind == "text"
        )

    def flush(self) -> str:
        """Emit whatever is safely left once the stream ends."""
        return "".join(
            chunk for kind, chunk in self._splitter.flush() if kind == "text"
        )
