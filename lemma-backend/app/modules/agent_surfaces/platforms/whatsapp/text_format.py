"""Translate and sanitize free-form text for WhatsApp delivery.

WhatsApp renders a small native subset — ``*bold*``, ``_italic_``,
``~strike~``, ````` ``` ````` for monospace — and nothing else. The agents
that author outbound messages write normal Markdown, and when any of it leaks
through we get the symptom this fixes: literal ``*asterisks*`` on the phone
instead of bold. Two things make WhatsApp show markers verbatim:

* Markdown with no WhatsApp mapping (``**bold**``, ``# heading``,
  ``[t](url)``, `` `code` ``) is passed through as-is, so it renders literally.
* WhatsApp drops *all* native formatting in a message the moment its
  delimiters unbalance — a stray ``*``, a bare ``*`` used as a bullet, an odd
  trailing marker. One stray ``*`` and every ``*bold*`` in that message renders
  literally.

``to_whatsapp_text`` converts the Markdown that maps onto WhatsApp's subset,
leaves syntax WhatsApp already understands untouched, and reduces everything
else to plain text so no raw marker leaks. ``to_plain_text`` is the aggressive
variant for media captions, where WhatsApp never applies formatting at all —
there markers are removed entirely so the caption reads clean.
"""

from __future__ import annotations

import re

# 1..6 heading prefix at the start of a line: ``# Heading`` -> ``Heading``.
_HEADING_PATTERN = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.*)$")

# Markdown strong: ``**bold**`` -> ``*bold*``.
_STRONG_PATTERN = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)

# Markdown inline code: `` `code` `` -> ``` ``code`` ``` (WhatsApp monospace).
_INLINE_CODE_PATTERN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")

# Markdown link: ``[label](url)`` -> bare ``url`` (WhatsApp guidance: paste the
# bare URL).
_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")

# A lone ``*`` on its own line — a bare bullet, not formatting. Dropping it
# keeps the message's delimiters balanced so it cannot nuke nearby bold.
_BARE_ASTERISK_BULLET_PATTERN = re.compile(r"(?m)^[ \t]*\*[ \t]*$")

# Stray delimiters hugging whitespace and/or a line edge (no text both sides).
# These are the unbalanced markers that kill the rest of the message's
# formatting; we only drop a delimiter that is adjacent to whitespace or the
# start/end of a line, never one that touches text on both sides.
_STRAY_OPEN_PATTERN = re.compile(r"(?:^|(?<=\s))[*_~](?=\s)")
_STRAY_CLOSE_PATTERN = re.compile(r"(?<=\s)[*_~](?:$|(?=\s))")


def to_whatsapp_text(text: str) -> str:
    """Map Markdown onto WhatsApp's subset and balance its delimiters.

    Text the author already wrote in WhatsApp syntax (``*bold*``, ``_italic_``,
    ``~strike~``, ``` ``` ```code``` ``) is preserved byte-for-byte. Markdown
    with a mapping is translated; Markdown with no mapping (headings, links,
    code fences) is reduced to plain text.
    """
    if not text:
        return text

    result = text

    # Headings have no WhatsApp equivalent — strip the prefix, keep the words.
    result = _HEADING_PATTERN.sub(lambda m: m.group(1), result)

    # Links: emit the bare URL, matching the platform's WhatsApp guidance.
    result = _LINK_PATTERN.sub(lambda m: m.group(2), result)

    # Paired code fences: keep the code, drop the fence lines. A single
    # triple-backtick monospace span (WhatsApp native) is left untouched.
    result = _strip_fenced_code_blocks(result)

    # Markdown strong -> WhatsApp bold.
    result = _STRONG_PATTERN.sub(r"*\1*", result)

    # Markdown inline code -> WhatsApp monospace (triple backticks).
    result = _INLINE_CODE_PATTERN.sub(lambda m: "```" + m.group(1) + "```", result)

    # A bare ``*`` bullet is not bold — drop it so it stays balanced.
    result = _BARE_ASTERISK_BULLET_PATTERN.sub("", result)

    # Drop stray delimiters hugging whitespace (fixpoint: early drops can expose
    # new edges).
    for _ in range(4):
        before = result
        result = _STRAY_OPEN_PATTERN.sub("", result)
        result = _STRAY_CLOSE_PATTERN.sub("", result)
        if result == before:
            break

    return result.strip()


def to_plain_text(text: str) -> str:
    """Strip every formatting marker to plain text (for media captions).

    WhatsApp never renders Markdown in captions, so a caption carrying
    ``*emphasis*``, ``**bold**`` or ``` ```code``` `` would show the literal
    markers. This removes them and collapses the text to clean lines.
    """
    if not text:
        return text

    result = _HEADING_PATTERN.sub(lambda m: m.group(1), text)
    result = _LINK_PATTERN.sub(lambda m: m.group(2), result)
    result = _strip_fenced_code_blocks(result)
    # Remove any run of formatting delimiters (backticks, asterisks, tilde).
    # Underscores are common in identifiers, so a ``_`` pair is only removed when
    # it does not touch word characters on either side (a genuine italic marker).
    result = re.sub(r"`{1,3}|\*{1,2}|~{1,2}", "", result)
    # Underscores are common in identifiers, so a ``_`` pair is only treated as
    # an italic marker when it wraps a word: ``_word_`` -> ``word``. A word-
    # internal ``_`` (``a_b``, ``var_name``) is an identifier, not formatting,
    # and is kept.
    result = re.sub(r"(?<!\w)_([^\s_]+)_(?!\w)", r"\1", result)
    # A stray single ``_`` not touching a word on either side is dropped.
    result = re.sub(r"(?<!\w)_(?!\w)", "", result)
    # Collapse leftover whitespace and blank lines.
    result = re.sub(r"[ \t]*\n[ \t]*\n+", "\n", result)
    result = re.sub(r" {2,}", " ", result)
    return result.strip()


def _strip_fenced_code_blocks(text: str) -> str:
    """Remove *paired* backtick/tilde code fences, keeping the code lines.

    Only a real block (an opening fence line, content, a closing fence line) is
    touched; a single-line triple-backtick monospace span is WhatsApp-native and
    deliberately left alone so ``to_whatsapp_text`` cannot mangle it.
    """
    fenced = re.compile(r"(?ms)^[ \t]*`{3,}[^\n]*\n(?P<body>.*?)\n^[ \t]*`{3,}[^\n]*$")
    result = fenced.sub(lambda m: m.group("body"), text)
    tilde = re.compile(r"(?ms)^[ \t]*~{3,}[^\n]*\n(?P<body>.*?)\n^[ \t]*~{3,}[^\n]*$")
    return tilde.sub(lambda m: m.group("body"), result)
