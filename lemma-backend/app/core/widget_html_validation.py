"""HTML checks for the shared app/widget browser-SDK contract.

``lint_app_html`` remains advisory for app uploads: callers log common authoring
mistakes without rejecting a bundle. ``validate_widget_html`` promotes those same
mistakes plus fragment/example/loader checks to blocking errors before an inline
widget is persisted and rendered. A widget is an HTML fragment: encoded content,
standalone SVG images, and markup that would not parse as authored are rejected
there rather than rendered as-is.

The browser SDK is served only from the API origin, so widgets and apps must build
its URL from the injected ``window.__LEMMA_CONFIG__.apiUrl`` and boot their code
from the dynamically created script's load handler.
"""

from __future__ import annotations

import re


_LINT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"@babel/standalone|type\s*=\s*['\"]text/babel['\"]", re.IGNORECASE),
        "Runtime Babel (`@babel/standalone` / `text/babel`) is not supported for apps — precompile JSX or use plain JS.",
    ),
    (
        re.compile(
            r"@lemma/pod-client|\bLemmaPodClient\b|\bcreateIframeTokenProvider\b"
        ),
        (
            "Uses the retired `@lemma/pod-client` SDK. Load `/public/sdk/lemma-client.js` and use "
            "`new window.LemmaClient.LemmaClient()` instead."
        ),
    ),
    (
        re.compile(
            r"<script[^>]+src=['\"][^'\"]*/(?:public/sdk|sdk)/pod-client\.js['\"]",
            re.IGNORECASE,
        ),
        'Loads the retired `pod-client.js`. Use `<script src="/public/sdk/lemma-client.js"></script>`.',
    ),
    (
        re.compile(
            r"\bsrc\s*=\s*['\"]https?://[^'\"]*/public/sdk/lemma-client\.js['\"]",
            re.IGNORECASE,
        ),
        (
            "Hardcodes an absolute host for the SDK script. Build the URL from "
            "`window.__LEMMA_CONFIG__.apiUrl` (the API origin) and load it in a dynamically "
            "created `<script>` that boots in `onload` — never the app's own subdomain."
        ),
    ),
    (
        re.compile(
            r"\bsrc\s*=\s*['\"]/(?:public/sdk|sdk)/lemma-(?:client|ui)\.js['\"]",
            re.IGNORECASE,
        ),
        (
            "Loads an SDK bundle (`lemma-client.js` / `lemma-ui.js`) with a relative "
            "`/public/sdk/...` src, which 404s on app subdomains (only the API origin serves "
            "the SDK). Build the URL from `window.__LEMMA_CONFIG__.apiUrl` and boot in the "
            "script's `onload` — see the `lemma-widget` skill's \"Loading the SDK\"."
        ),
    ),
    (
        re.compile(r"new\s+window\.LemmaClient\s*\("),
        (
            "`new window.LemmaClient(...)` references the namespace object, not the constructor — "
            "use `new window.LemmaClient.LemmaClient()`."
        ),
    ),
)

_HARDCODED_POD_ID = re.compile(
    r"podId\s*:\s*['\"][0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}['\"]"
)
_FULL_DOCUMENT = re.compile(
    r"<!doctype|<html[\s>]|<head[\s>]|<body[\s>]", re.IGNORECASE
)
# Content carrying no element tag at all is an encoding mistake (base64, an
# unresolved placeholder), not markup. Every other rule here matches known-bad
# *markup*, so an encoded blob trips none of them and would render as literal
# text in the iframe. Checked first so the error names the actual mistake.
_ELEMENT_TAG = re.compile(r"<[a-zA-Z][^>]*>")
# How much of a rejected value the error quotes back. Long enough that the
# author recognises what it sent, short enough that a whole failed fragment
# does not land in the transcript twice.
_EXCERPT_CHARACTERS = 80
# A widget is a view, not an image. An SVG-rooted fragment belongs in pod files,
# where it is addressable and reusable, and displays via FILE. Only the root is
# rejected — inline <svg> icons inside an HTML fragment stay fine.
#
# Leading comments are stripped by hand rather than folded into this pattern:
# `(?:<!--.*?-->\s*)*<svg` backtracks exponentially on input that opens a comment
# and never reaches an <svg> (`"<!--" + "--><!--" * n`), and widget content comes
# from the agent, so that is reachable.
_SVG_ROOT = re.compile(r"\A<svg\b", re.IGNORECASE)
# What actually opens a tag. "<" is only the start of one when an ASCII letter
# (an element) or "!" (a comment) follows it; before anything else the parser
# emits a literal "<" as text and carries on. Testing `startswith("<")` instead
# read as the same rule and was not: `<<style>` satisfied it, shipped, and put a
# bare "<" on the line above the view -- the exact thing the rule exists to stop.
_TAG_OPEN = re.compile(r"<(?:[a-zA-Z]|!)")
_UNRESOLVED_TEMPLATE_TOKEN = re.compile(r"__[A-Z][A-Z0-9_]*__")
_RUNTIME_CONFIG_REFERENCE = re.compile(r"\b(?:window\.)?__LEMMA_CONFIG__\b")
_API_URL_IDENTIFIER = re.compile(r"\bapiUrl\b")

# CSS authored outside a <style> element renders as literal text in the served
# document (a "text salad" of rules), so it is rejected before display. The
# block/tag strippers isolate the fragment's bare text first, which keeps inline
# style="" attributes, <script> object literals, and real stylesheet contents
# from tripping the rule-shaped match.
# Closing tags may carry whitespace and ignored attributes after the tag name
# (</script\t\n foo> still ends the element), so the matcher allows any run of
# non-">" characters there, bounded by "\b" to keep the name exact.
_STYLE_BLOCK = re.compile(
    r"<style\b[^>]*>.*?</style\b[^>]*>", re.IGNORECASE | re.DOTALL
)
_SCRIPT_BLOCK = re.compile(
    r"<script\b[^>]*>.*?</script\b[^>]*>", re.IGNORECASE | re.DOTALL
)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ANY_TAG = re.compile(r"<[^>]+>")
# The rule shape is walked brace-first rather than matched by one regex. As a
# single pattern (`[^{}\s<>][^{}<>]*\{[^{}]*[a-zA-Z-]+\s*:[^{}]*\}`) the selector
# run has to be retried from every offset in the text, which is quadratic: 32KB
# of brace-free prose took 4.7s and 128KB took 75s, on content the agent writes.
# Anchoring on "{" makes each candidate block O(1) to reach.
_CSS_DECLARATION = re.compile(r"[a-zA-Z-]\s*:")

# A tag that lost its opening "<" survives as a text node: the element never
# exists, so its styling never applies and its own markup renders as literal
# text. `_NAKED_CSS_RULE` cannot see this, because the styling that goes missing
# sits in an inline style="" attribute and carries no braces to match on. The
# signature is an element name followed by a quoted attribute; prose and code
# samples reach `= "` with no element name in front of it. Content that escaped
# its markup on purpose spells the bracket `&lt;`, so that spelling is excluded.
_LEAKED_TAG_TEXT = re.compile(
    r"(?<!&lt;)\b(?:a|article|aside|button|canvas|code|div|em|footer|form|h[1-6]"
    r"|header|img|input|label|li|main|nav|ol|p|path|pre|section|small|span"
    r"|strong|svg|table|tbody|td|th|thead|tr|ul)"
    r"\s+[a-zA-Z][a-zA-Z0-9-]*\s*=\s*[\"']"
)
# The mirror of the same mistake: the end tag whose start tag became text closes
# nothing. End tags are matched against the open stack by name rather than by
# exact nesting, because `<ul><li>a<li>b</ul>` is valid HTML and its implied end
# tags would make a strict stack report a phantom error.
_TAG_TOKEN = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>")
_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def _without_leading_comments(content: str) -> str:
    """Drop leading HTML comments in one linear pass (no backtracking)."""
    while content.startswith("<!--"):
        end = content.find("-->", len("<!--"))
        if end == -1:
            return content
        content = content[end + len("-->") :].lstrip()
    return content


def lint_app_html(html: str) -> list[str]:
    """Return advisory warnings for app/widget HTML. Never raises; ``[]`` == clean."""
    warnings: list[str] = []
    for pattern, message in _LINT_PATTERNS:
        if pattern.search(html):
            warnings.append(message)
    if _HARDCODED_POD_ID.search(html):
        warnings.append(
            "Hardcoded pod id passed to the SDK. Construct "
            "`new window.LemmaClient.LemmaClient()` with no args; the host injects "
            "`window.__LEMMA_CONFIG__`."
        )
    return warnings


def _stripped_blocks(html: str) -> str:
    """``html`` with <style>/<script> elements and comments removed."""
    text = _STYLE_BLOCK.sub("", html)
    text = _SCRIPT_BLOCK.sub("", text)
    return _HTML_COMMENT.sub("", text)


def _stray_end_tag(html: str) -> str | None:
    """Name of the first end tag that closes nothing, else None."""
    open_tags: list[str] = []
    for closing, raw_name, attributes in _TAG_TOKEN.findall(_stripped_blocks(html)):
        name = raw_name.lower()
        if not closing:
            if name not in _VOID_ELEMENTS and not attributes.rstrip().endswith("/"):
                open_tags.append(name)
        elif name in open_tags:
            while open_tags.pop() != name:
                pass
        else:
            return name
    return None


def _malformed_markup_error(html: str) -> str | None:
    """An error when the fragment parses as something other than it reads, else None."""
    if _LEAKED_TAG_TEXT.search(_ANY_TAG.sub(" ", _stripped_blocks(html))):
        return (
            "A tag is missing its opening '<', so it renders as literal text and "
            "the element it should have opened never exists — everything that tag "
            "styled is left unstyled. Check the fragment's markup."
        )
    stray = _stray_end_tag(html)
    if stray is not None:
        return (
            f"</{stray}> closes an element that was never opened, so the fragment "
            "does not nest the way it reads. Check for a tag with a missing '<'."
        )
    return None


def _next_brace(text: str, start: int) -> int:
    """Index of the next "{" or "}" at or after ``start``, or -1."""
    opened, closed = text.find("{", start), text.find("}", start)
    if opened == -1:
        return closed
    return opened if closed == -1 else min(opened, closed)


def _has_naked_css_rule(text: str) -> bool:
    """True when ``text`` holds a ``selector { property: value }`` rule."""
    start = 0
    while (open_brace := text.find("{", start)) != -1:
        end = _next_brace(text, open_brace + 1)
        if end == -1:
            return False
        if text[end] == "}" and _CSS_DECLARATION.search(text[open_brace + 1 : end]):
            # The selector is the run of plain text ending at "{": whatever
            # follows the last brace or angle bracket before it. Whitespace
            # alone is a bare block (a script's object literal), not a rule.
            cut = max(text.rfind(char, 0, open_brace) for char in "<>{}")
            if text[cut + 1 : open_brace].strip():
                return True
        # An inner "{" opens a fresh candidate; a closed block is consumed.
        start = end if text[end] == "{" else end + 1
    return False


def _naked_css_error(html: str) -> str | None:
    """An error when CSS rules sit outside any ``<style>`` element, else None."""
    text = _ANY_TAG.sub(" ", _stripped_blocks(html))
    if _has_naked_css_rule(text):
        return (
            "CSS rules appear outside any <style> tag, so they would render as "
            "plain text. Wrap the widget's stylesheet in a <style>...</style> "
            "element."
        )
    return None


def _excerpt(content: str) -> str:
    """The head of a rejected value, quoted, for an error that names it."""
    head = " ".join(content[: _EXCERPT_CHARACTERS * 2].split())
    if len(head) > _EXCERPT_CHARACTERS:
        head = head[:_EXCERPT_CHARACTERS].rstrip() + "…"
    return repr(head)


def _leading_text_error(content: str) -> str | None:
    """An error when the fragment opens with text instead of markup, else None.

    A fragment begins with a tag. Text in front of one is the sentence that was
    meant for the person reading the reply — "clean version below", "ignore the
    stray line", a title for the view — and it renders as a bare unstyled line
    above the widget, outside every rule the fragment carries.

    It is worth its own rule because of what it is a symptom of. The reply and
    the argument are two channels, and the widget calls that fail worst are the
    ones where they blur: a whole argument of narration where the fragment
    should be, which the empty-markup check above catches, and this, its
    half-measure, which nothing else does — the markup is all there, so every
    other rule passes and the stray line ships.

    A single stray character is the same mistake and reads as nothing at all in
    the source, which is why the test is "opens a tag" rather than "opens with
    a bracket": `<<style>` begins with "<" and still puts a "<" on the line
    above the view.
    """
    if _TAG_OPEN.match(content):
        return None
    # Everything before the first thing that *does* open a tag. For prose that is
    # the sentence; for `<<style>` it is the one character nobody meant to type.
    first_tag = _TAG_OPEN.search(content)
    opening = content[: first_tag.start()] if first_tag else content
    return (
        f"Widget content must begin with a tag, and this begins with "
        f"{_excerpt(opening)}. Anything in front of the first tag renders as a "
        "bare line above the view, unstyled and outside the fragment — a "
        "sentence meant for the person, or a single stray character before "
        "`<style>`. Explanation belongs in your reply, not in `content`."
    )


def _sdk_loader_errors(content: str) -> list[str]:
    """Errors in how a data-backed fragment reaches the browser SDK, else []."""
    if "lemma-client.js" not in content and "LemmaClient" not in content:
        return []
    errors: list[str] = []
    if not _RUNTIME_CONFIG_REFERENCE.search(content):
        errors.append(
            "SDK-backed widgets must read window.__LEMMA_CONFIG__ at runtime."
        )
    if not _API_URL_IDENTIFIER.search(content):
        errors.append("Build the browser SDK URL from window.__LEMMA_CONFIG__.apiUrl.")
    if "lemma-client.js" not in content:
        errors.append(
            "The widget uses LemmaClient but does not load /public/sdk/lemma-client.js."
        )
    if not re.search(
        r"\.onload\s*=|addEventListener\(\s*['\"]load['\"]",
        content,
        re.IGNORECASE,
    ):
        errors.append("Boot SDK-backed widget code from the SDK script's load handler.")
    return errors


def validate_widget_html(html: str) -> list[str]:
    """Return blocking authoring errors for an inline widget fragment."""
    content = (html or "").strip()
    if not content:
        return ["Widget content must not be empty."]

    if not _ELEMENT_TAG.search(content):
        return [
            (
                "Widget content must be an HTML fragment — no element tag found "
                f"in the {len(content)} characters received ({_excerpt(content)}). "
                "The whole fragment goes in this one `content` argument and is "
                "displayed the moment the call succeeds: a note about the markup, "
                "a placeholder standing in for it, or a promise to send it next is "
                "not markup, and no later call can complete this one. If the "
                "fragment is too long to write out here, serve it instead — save "
                "the HTML as an app and pass its address as `public_url`. Pass raw "
                "markup, not base64 or any other encoded form."
            )
        ]

    if _SVG_ROOT.match(_without_leading_comments(content)):
        return [
            (
                "Widget content must be an HTML fragment, not a standalone SVG. "
                "Upload the image with `lemma files upload` and show it with "
                'display_resource(type="FILE", path=...).'
            )
        ]

    errors = list(lint_app_html(content))
    leading = _leading_text_error(content)
    if leading:
        errors.append(leading)
    if _FULL_DOCUMENT.search(content):
        errors.append(
            "Widget content must be an HTML fragment without doctype, html, head, or body tags."
        )
    malformed = _malformed_markup_error(content)
    if malformed:
        errors.append(malformed)
    naked_css = _naked_css_error(content)
    if naked_css:
        errors.append(naked_css)

    tokens = sorted(
        set(_UNRESOLVED_TEMPLATE_TOKEN.findall(content)) - {"__LEMMA_CONFIG__"}
    )
    if tokens:
        errors.append(
            "Replace unresolved widget example placeholders before display: "
            + ", ".join(tokens)
            + "."
        )

    errors.extend(_sdk_loader_errors(content))

    return list(dict.fromkeys(errors))
