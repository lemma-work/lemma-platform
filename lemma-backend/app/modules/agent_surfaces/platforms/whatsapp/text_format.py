"""Translate and sanitize free-form text for WhatsApp delivery.

WhatsApp renders a small native subset — ``*bold*``, ``_italic_``,
``~strike~``, ````` ``` ````` for monospace, ``- `` bullets, ``> `` quotes —
and nothing else. The agents that author outbound messages write normal
Markdown, and when any of it leaks through we get the symptom this fixes:
literal ``*asterisks*`` on the phone instead of bold. Two things make WhatsApp
show markers verbatim:

* Markdown with no WhatsApp mapping (``**bold**``, ``# heading``,
  ``[t](url)``, `` `code` ``) is passed through as-is, so it renders literally.
* A delimiter that never finds a partner (``*bold`` with no closing marker)
  renders as a literal character in the middle of a sentence.

``to_whatsapp_text`` converts the Markdown that maps onto WhatsApp's subset,
leaves syntax WhatsApp already understands untouched, and reduces everything
else to plain text so no raw marker leaks. ``to_plain_text`` is the aggressive
variant for media captions, where WhatsApp never applies formatting at all —
there markers are removed entirely so the caption reads clean.

Both share one rule about stray delimiters, and it is deliberately narrower
than "delete anything suspicious". A delimiter is only removed when it is
*unpaired and hugging text on exactly one side* — the shape of a genuinely
broken marker. A delimiter with whitespace on both sides is something the
author typed as a character (``2 * 3 = 6``, ``approx ~5 items``), and deleting
it silently rewrites the message; one with text on both sides is an identifier
(``var_name``). Both survive.
"""

from __future__ import annotations

import re

_DELIMITERS = ("*", "_", "~")

# 1..6 heading prefix at the start of a line: ``# Heading`` -> ``*Heading*``.
# WhatsApp has no headings, but it has bold, and a heading reduced to bare prose
# loses the structure the author was signalling.
_HEADING_PATTERN = re.compile(
    r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(?P<text>.+?)[ \t]*#*[ \t]*$"
)

# Markdown strong: ``**bold**`` / ``__bold__`` -> ``*bold*``. Deliberately not
# DOTALL: WhatsApp's bold does not span a newline, so a match that crossed one
# would emit a pair the platform renders as two literal asterisks.
_STRONG_STAR_PATTERN = re.compile(r"\*\*(?P<text>[^\n*]+?)\*\*")
_STRONG_UNDERSCORE_PATTERN = re.compile(r"__(?P<text>[^\n_]+?)__")

# Markdown inline code: `` `code` `` -> ``` ``code`` ``` (WhatsApp monospace).
_INLINE_CODE_PATTERN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")

# Markdown image: ``![alt](url)`` -> the URL. Matched before links so the
# leading ``!`` goes with it instead of being left stranded.
_IMAGE_PATTERN = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)\)")

# Markdown link: ``[label](url)`` -> bare ``url`` (WhatsApp guidance: paste the
# bare URL, which the client auto-links).
_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")

# A thematic break (``---``, ``***``, ``___``) has no WhatsApp equivalent, and
# the asterisk/underscore forms are stray delimiters if left in place.
_THEMATIC_BREAK_PATTERN = re.compile(
    r"(?m)^[ \t]{0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})$"
)

# List bullets written with ``*`` or ``+``. WhatsApp renders ``- `` as a native
# bullet, so the marker is translated rather than deleted — dropping it flattens
# a list into an unmarked run of lines.
_STAR_BULLET_PATTERN = re.compile(r"(?m)^(?P<indent>[ \t]*)[*+][ \t]+(?=\S)")

# A lone ``*``/``+``/``-`` on its own line — an empty bullet, not formatting.
_EMPTY_BULLET_PATTERN = re.compile(r"(?m)^[ \t]*[*+-][ \t]*$")

# A markdown table separator row (``|---|:--:|``) is pure layout scaffolding.
_TABLE_SEPARATOR_PATTERN = re.compile(
    r"(?m)^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)+\|?[ \t]*$"
)

# A markdown table row. WhatsApp has no tables and a pipe grid on a phone is
# unreadable, so cells are joined into one line.
_TABLE_ROW_PATTERN = re.compile(r"(?m)^[ \t]*\|(?P<cells>.+)\|[ \t]*$")

# Markdown strong that survived translation because it spans a newline.
_LEFTOVER_STRONG_RUN_PATTERN = re.compile(r"\*{2,}")
_BLANK_RUN_PATTERN = re.compile(r"\n{3,}")

# Code fences are stashed under a placeholder while the rest is rewritten. The
# NUL terminator cannot occur in a WhatsApp message body, so nothing an author
# writes can collide with it.
_FENCE_PLACEHOLDER = "\x00lemma-fence-"
_FENCE_PLACEHOLDER_PATTERN = re.compile(r"\x00lemma-fence-(?P<index>\d+)\x00")

_FENCED_BACKTICK_PATTERN = re.compile(
    r"(?ms)^[ \t]*`{3,}[^\n]*\n(?P<body>.*?)\n^[ \t]*`{3,}[ \t]*$"
)
_FENCED_TILDE_PATTERN = re.compile(
    r"(?ms)^[ \t]*~{3,}[^\n]*\n(?P<body>.*?)\n^[ \t]*~{3,}[ \t]*$"
)


def to_whatsapp_text(text: str) -> str:
    """Map Markdown onto WhatsApp's subset and balance its delimiters.

    Text the author already wrote in WhatsApp syntax (``*bold*``, ``_italic_``,
    ``~strike~``, ``` ``` ```code``` ``) is preserved byte-for-byte. Markdown
    with a mapping is translated; Markdown with no mapping (headings, links,
    tables) is reduced to the nearest thing WhatsApp can show.
    """
    if not text:
        return text

    result = text.replace("\r\n", "\n").replace("\r", "\n")

    # Fenced code blocks come out first and go back in last. Everything between
    # is a Markdown rewrite, and a code block is the one place where ``**``,
    # ``# ``, and ``|`` are literal characters the author wants delivered
    # verbatim — rewriting them would corrupt the code being shown.
    result, blocks = _extract_code_fences(result)

    # Images before links: the pattern for a link also matches the ``[alt](url)``
    # tail of an image, which would leave a stranded ``!``.
    result = _IMAGE_PATTERN.sub(lambda m: m.group("url"), result)
    result = _LINK_PATTERN.sub(lambda m: m.group(2), result)

    result = _THEMATIC_BREAK_PATTERN.sub("", result)
    result = _flatten_tables(result)

    # Headings have no WhatsApp equivalent; bold is the closest thing it has.
    result = _HEADING_PATTERN.sub(lambda m: f"*{m.group('text')}*", result)

    # Markdown strong -> WhatsApp bold.
    result = _STRONG_STAR_PATTERN.sub(lambda m: f"*{m.group('text')}*", result)
    result = _STRONG_UNDERSCORE_PATTERN.sub(lambda m: f"*{m.group('text')}*", result)
    # Any ``**`` left over spans a newline, which WhatsApp's bold cannot. Collapsed
    # to a single marker so the balancing pass below can judge it; left as a run it
    # would sail past that pass and land on the phone as literal asterisks.
    result = _LEFTOVER_STRONG_RUN_PATTERN.sub("*", result)

    # Markdown inline code -> WhatsApp monospace (triple backticks).
    result = _INLINE_CODE_PATTERN.sub(lambda m: "```" + m.group(1) + "```", result)

    # ``* item`` / ``+ item`` -> ``- item``, which WhatsApp bullets natively.
    result = _STAR_BULLET_PATTERN.sub(lambda m: f"{m.group('indent')}- ", result)
    result = _EMPTY_BULLET_PATTERN.sub("", result)

    result = balance_whatsapp_delimiters(result)
    result = _restore_code_fences(result, blocks)
    # Dropped rules and empty bullets leave runs of blank lines behind.
    result = _BLANK_RUN_PATTERN.sub("\n\n", result)
    return result.strip()


def balance_whatsapp_delimiters(text: str) -> str:
    """Drop delimiters that can never pair, leaving literal characters alone.

    Exposed because truncation happens *after* translation — slicing a message
    at 4096 characters can cut a ``*bold*`` pair in half, which is exactly the
    broken marker this module exists to prevent. Re-run it on anything that was
    cut.
    """
    if not text:
        return text
    lines = text.split("\n")
    for delimiter in _DELIMITERS:
        lines = [_drop_unpaired(line, delimiter) for line in lines]
    return "\n".join(lines)


def to_plain_text(text: str) -> str:
    """Strip every formatting marker to plain text (for media captions).

    WhatsApp never renders Markdown in captions, so a caption carrying
    ``*emphasis*``, ``**bold**`` or ``` ```code``` `` would show the literal
    markers. Paired markers are removed; a character the author typed as itself
    (``2 * 3``, ``approx ~5``, ``var_name``) is kept.
    """
    if not text:
        return text

    result = to_whatsapp_text(text)
    result = re.sub(r"`{3}", "", result)
    for delimiter in _DELIMITERS:
        result = "\n".join(_drop_paired(line, delimiter) for line in result.split("\n"))
    # Collapse leftover whitespace and blank lines.
    result = re.sub(r"[ \t]*\n[ \t]*\n+", "\n", result)
    result = re.sub(r" {2,}", " ", result)
    return result.strip()


def _extract_code_fences(text: str) -> tuple[str, list[str]]:
    """Replace paired fences with placeholders; return the text and the blocks.

    The block that comes back is WhatsApp monospace — the fence is kept, because
    WhatsApp does render a multi-line ``` block and code reduced to bare prose is
    indistinguishable from the sentence above it. Only the ``py`` / ``bash`` info
    string is dropped, since WhatsApp would print it as the block's first line.
    """
    blocks: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        blocks.append("```\n" + match.group("body") + "\n```")
        return f"{_FENCE_PLACEHOLDER}{len(blocks) - 1}\x00"

    result = _FENCED_BACKTICK_PATTERN.sub(_stash, text)
    result = _FENCED_TILDE_PATTERN.sub(_stash, result)
    return result, blocks


def _restore_code_fences(text: str, blocks: list[str]) -> str:
    if not blocks:
        return text
    return _FENCE_PLACEHOLDER_PATTERN.sub(lambda m: blocks[int(m.group("index"))], text)


def _flatten_tables(text: str) -> str:
    """Turn a Markdown table into one line per row, ``a — b — c``.

    A pipe grid on a phone wraps into nonsense and the ``|---|`` rule row is
    pure scaffolding. Neither carries meaning WhatsApp can show, so the cells
    become a readable line instead.
    """
    result = _TABLE_SEPARATOR_PATTERN.sub("", text)

    def _row(match: re.Match[str]) -> str:
        cells = [cell.strip() for cell in match.group("cells").split("|")]
        return " — ".join(cell for cell in cells if cell)

    return _TABLE_ROW_PATTERN.sub(_row, result)


def _flanking(line: str, index: int) -> tuple[bool, bool]:
    """Whether the delimiter at ``index`` can open and/or close a span.

    WhatsApp will not open a span whose first character is whitespace, nor close
    one whose last character is whitespace, so a marker's role is decided by its
    neighbours (a line edge counts as whitespace):

    * whitespace before, text after -> it can only open;
    * text before, whitespace after -> it can only close;
    * text on both sides -> part of a word (``var_name``, a URL path), not a
      marker at all;
    * whitespace on both sides -> a character the author typed (``2 * 3``).

    The last two cases return ``(False, False)``: neither pairs, and neither is
    ever removed.
    """
    left = line[index - 1] if index > 0 else " "
    right = line[index + 1] if index + 1 < len(line) else " "
    left_is_space = left.isspace()
    right_is_space = right.isspace()
    if left_is_space == right_is_space:
        return False, False
    return right_is_space is False, left_is_space is False


def _delimiter_positions(line: str, delimiter: str) -> list[int]:
    """Single occurrences of ``delimiter``, skipping runs (``**``, ``~~``).

    A run is either already-translated syntax or something the author repeated
    on purpose; pairing individual characters out of it would corrupt it.
    """
    positions: list[int] = []
    index = 0
    length = len(line)
    while index < length:
        if line[index] != delimiter:
            index += 1
            continue
        run_end = index
        while run_end < length and line[run_end] == delimiter:
            run_end += 1
        if run_end - index == 1:
            positions.append(index)
        index = run_end
    return positions


def _pair(line: str, delimiter: str) -> tuple[set[int], set[int]]:
    """Greedily pair markers on one line; return ``(paired, unpaired)``.

    Only markers that flank text on exactly one side take part — the rest are
    literal characters and appear in neither set, so neither pass touches them.
    """
    paired: set[int] = set()
    unpaired: set[int] = set()
    open_index: int | None = None
    for index in _delimiter_positions(line, delimiter):
        can_open, can_close = _flanking(line, index)
        if open_index is None:
            if can_open:
                open_index = index
            elif can_close:
                unpaired.add(index)
            continue
        if can_close:
            paired.add(open_index)
            paired.add(index)
            open_index = None
        else:
            # A second opener with no close between them: the first one is the
            # broken marker, and this one may still find a partner.
            unpaired.add(open_index)
            open_index = index
    if open_index is not None:
        unpaired.add(open_index)
    return paired, unpaired


def _drop_unpaired(line: str, delimiter: str) -> str:
    """Remove markers that flank text but never found a partner.

    That one-sided shape is what a broken marker looks like — ``*bold`` with no
    close, or a pair cut in half by truncation. Everything ``_pair`` declined to
    classify is a character the author typed, and stays.
    """
    _, unpaired = _pair(line, delimiter)
    if not unpaired:
        return line
    return "".join(char for i, char in enumerate(line) if i not in unpaired)


def _drop_paired(line: str, delimiter: str) -> str:
    """Remove only the delimiters that form a real pair (caption stripping)."""
    paired, _ = _pair(line, delimiter)
    if not paired:
        return line
    return "".join(char for i, char in enumerate(line) if i not in paired)
