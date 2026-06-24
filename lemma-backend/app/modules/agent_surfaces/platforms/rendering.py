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

# Characters MarkdownV2 reserves in normal text; each must be backslash-escaped.
_MD_V2_RESERVED = set(r"_*[]()~`>#+-=|{}.!\\")
# Ordered alternation: fenced code and inline code first (so their contents are
# protected), then bold/strikethrough before italic (so ``**`` is not mistaken
# for two italics), then links.
_TOKEN_RE = re.compile(
    r"```[\s\S]*?```"            # fenced code block
    r"|`[^`\n]+`"                # inline code
    r"|\*\*[\s\S]+?\*\*"         # **bold**
    r"|__[\s\S]+?__"             # __bold__
    r"|~~[\s\S]+?~~"             # ~~strikethrough~~
    r"|\*[^*\n]+?\*"             # *italic*
    r"|_[^_\n]+?_"               # _italic_
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


def chunk_text(text: str, *, limit: int) -> list[str]:
    """Split ``text`` into chunks no longer than ``limit`` characters.

    Prefers paragraph (``\\n\\n``) then line (``\\n``) then word boundaries;
    hard-splits only a single run longer than ``limit``. Safe to call on
    already-rendered MarkdownV2 because it never cuts an escape pair.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
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
# Some OpenAI-compatible models (e.g. Fireworks MiniMax M3) emit chain-of-
# thought reasoning inline in the text content as ``<think>…</think>`` tags
# rather than as a separate reasoning/thinking part. Without stripping, these
# tags leak to surfaces as normal chat messages. The regex below removes:
#
#   - self-closing tags: ``<think/>``, ``<thinking foo="bar"/>``
#   - closed blocks:     ``<think>reasoning</think>``
#   - unclosed blocks:   ``<think>reasoning`` (model forgot to close — strip
#                         everything from the open tag to end of string)
#
# Case-insensitive to handle ``<THINK>``, ``<Think>``, etc.
_THINK_TAG_RE = re.compile(
    r"<think(?:ing)?[^>]*/>"  # self-closing (empty)
    r"|<think(?:ing)?[^>]*>.*?</think(?:ing)?>"  # closed block (non-greedy)
    r"|<think(?:ing)?[^>]*>.*",  # unclosed open tag to end (greedy)
    re.DOTALL | re.IGNORECASE,
)


def strip_thinking_tokens(text: str) -> str:
    """Remove ``<think>…</think>`` reasoning blocks from model output.

    Returns the text with all thinking/reasoning blocks removed and
    surrounding whitespace collapsed. If the text contains no thinking tags it
    is returned unchanged (after stripping). An empty/whitespace-only result
    after stripping returns ``""``.
    """
    if not text:
        return ""
    stripped = _THINK_TAG_RE.sub("", text)
    return stripped.strip()
