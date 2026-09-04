"""Reading an inbound email: markup to text, and the reply out of the thread.

Every provider hands us some mixture of a text part, an HTML part and a quoted
history, in a different shape each time. What the agent needs from all of them
is the same thing -- the words a person typed on this reply -- so the work of
getting there lives here once rather than in each parser.
"""

from __future__ import annotations

import base64
import binascii
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote

# Contents of these never render for a reader, so feeding them to a model is
# pure noise — a stylesheet inlined by a mail client can dwarf the message.
_NON_TEXT_TAGS = frozenset({"style", "script", "head", "title"})

# Tags that end a line for a reader. Without them every paragraph runs together
# and an HTML-only email arrives as one unbroken wall of text.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "tr",
        "li",
        "ul",
        "ol",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "section",
    }
)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _NON_TEXT_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _NON_TEXT_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data and not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        joined = unescape("".join(self._parts))
        # Collapse runs of spaces/tabs but keep line structure: paragraph breaks
        # are most of what makes a quoted reply or a list readable.
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        lines = [line.strip() for line in joined.split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# Generous against a long quoted thread and small enough that parsing stays
# bounded work. Inbound bodies are typically well under a tenth of this.
_MAX_HTML_CHARS = 1024 * 1024


def reply_subject(subject: str | None) -> str:
    clean = str(subject or "").strip()
    if not clean:
        return "Reply from Lemma"
    if clean.lower().startswith("re:"):
        return clean
    return f"Re: {clean}"


def plain_text_from_html(value: str | None) -> str:
    html_value = str(value or "").strip()
    if not html_value:
        return ""
    # `html.parser` is pure Python — roughly a megabyte a second — and the body
    # arrives from whoever sent the mail, so without a bound the time this
    # spends on the event loop is the sender's to choose. Past the cap the tail
    # is dropped rather than parsed: an agent reading a message does not need
    # the last megabyte of a thread that has been replied to two hundred times.
    truncated = len(html_value) > _MAX_HTML_CHARS
    if truncated:
        html_value = html_value[:_MAX_HTML_CHARS]
    parser = _HTMLTextExtractor()
    parser.feed(html_value)
    text = parser.text()
    if truncated:
        # Say so. An agent reading a silently-cut body has no way to tell the
        # message ended from the message being clipped, and will answer as if
        # it read the whole thing.
        text = f"{text}\n\n[message truncated: exceeded {_MAX_HTML_CHARS} characters]"
    return text


# Where a mail client starts quoting the message being replied to. Deliberately
# anchored to line starts: "On ... wrote:" appearing mid-sentence is prose, not
# a quote header.
# Deliberately only the markers that *open a quoted block*. `From:` and
# `Sent from my …` were here too and were actively destructive: both occur
# mid-message in ordinary mail ("From: the numbers you sent, I agree"), and both
# already sit inside the block that `On … wrote:` or `-----Original Message-----`
# anchors, so they bought nothing and truncated real content.
_QUOTE_MARKERS = (
    re.compile(r"^\s*On .{0,200}?wrote:\s*$", re.IGNORECASE | re.MULTILINE),
    # The same attribution, soft-wrapped. Gmail breaks a long one mid-address --
    # "On <date> Name <\n address> wrote:" -- and the single-line marker above
    # cannot see across the newline, so the whole quoted thread survived into
    # the message. On an email surface that is not just noise in the prompt: a
    # one-word reply like "approve" stopped being a decision, fell through to
    # the ordinary message path, and superseded the very approval it was
    # answering.
    #
    # Requiring an address in the span is what keeps this off ordinary prose
    # that happens to open with "On " and reach a "wrote:" a line later.
    re.compile(
        r"^\s*On .{0,300}?@.{0,200}?wrote:\s*$",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    ),
    re.compile(
        r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE | re.MULTILINE
    ),
    re.compile(r"^\s*_{5,}\s*$", re.MULTILINE),
)


# A forward's payload is *below* the marker — it is the whole reason the message
# was sent. Outlook writes "-----Original Message-----" for replies and forwards
# alike, so the marker cannot tell them apart and these have to.
_FORWARD_SUBJECT = re.compile(r"^\s*(fwd?|wg|tr|rv|enc)\s*:", re.IGNORECASE)
_FORWARD_MARKERS = (
    re.compile(r"^-+\s*Forwarded message\s*-+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Begin forwarded message:", re.IGNORECASE | re.MULTILINE),
)


def looks_forwarded(text: str | None, subject: str | None = None) -> bool:
    """Whether this message is somebody forwarding content *to* us."""
    if _FORWARD_SUBJECT.match(str(subject or "")):
        return True
    body = str(text or "")
    return any(marker.search(body) for marker in _FORWARD_MARKERS)


def strip_quoted_reply(text: str | None, subject: str | None = None) -> str:
    """Drop the quoted original from a reply, keeping what the person wrote.

    Every provider needs this and none had it. Without it each reply carries the
    whole thread forward, so by the fourth exchange most of the prompt is the
    agent re-reading its own earlier messages — which is both expensive and a
    reliable way to make it answer a question that was already settled.

    Conservative by construction: it only cuts at a marker that begins a line,
    and it keeps the original text whenever cutting would leave nothing, so a
    top-posted reply survives and a false positive costs context rather than the
    message.
    """
    body = str(text or "")
    if not body.strip():
        return ""

    # Forwarding an invoice, a bug report or a thread *is* the message. Trimming
    # at the marker leaves only "please handle this" and throws away the thing
    # to handle, so a forward is never trimmed.
    if looks_forwarded(body, subject):
        return body.strip()

    earliest = len(body)
    for marker in _QUOTE_MARKERS:
        match = marker.search(body)
        if match is not None:
            earliest = min(earliest, match.start())

    # "> " quoting: only cut when the quoted run reaches the end of the message.
    # A quote with prose after it is somebody pasting a log or an excerpt and
    # then saying something about it — cutting there deletes the actual message,
    # which is worse than carrying a few quoted lines into the prompt.
    lines = body.split("\n")
    offset = 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith(">") and body[:offset].strip():
            rest = lines[index:]
            if all(not text.strip() or text.lstrip().startswith(">") for text in rest):
                earliest = min(earliest, offset)
            break
        offset += len(line) + 1

    trimmed = body[:earliest].strip()
    return trimmed or body.strip()


def inbound_email_text(
    *,
    text: Any = None,
    html: Any = None,
    html_format: Any = None,
    subject: Any = None,
) -> str:
    """The message a person actually typed, from whichever part carries it.

    Prefers ``text`` and falls back to rendered HTML. ``html_format="data_uri"``
    is Resend's encoding for the HTML part, so it is decoded before being read
    as markup — treating it as raw HTML yields a base64 blob in the prompt.
    """
    plain = str(text or "").strip()
    if not plain:
        plain = plain_text_from_html(decode_email_html(html, html_format))
    return strip_quoted_reply(plain, subject)


def decode_email_html(html: Any, html_format: Any = None) -> str:
    """Resolve an HTML part that may arrive as a ``data:`` URI."""
    raw = str(html or "").strip()
    if not raw:
        return ""
    if str(html_format or "").strip().lower() == "data_uri" or raw.startswith("data:"):
        try:
            _, _, payload = raw.partition(",")
            if ";base64" in raw.split(",", 1)[0]:
                return base64.b64decode(payload).decode("utf-8", errors="replace")
            return unquote(payload)
        except ValueError, binascii.Error:
            # A malformed data URI is not worth losing the email over; fall
            # through and let the HTML extractor salvage what it can.
            return raw
    return raw
