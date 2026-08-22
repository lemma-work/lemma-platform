"""Making an agent's markdown look like an email somebody wrote on purpose.

Mail clients are the last place on the web where a stylesheet cannot be relied
on. Gmail strips `<style>` from forwarded mail, Outlook renders through Word,
and several clients drop `<head>` entirely -- so every rule has to ride on the
element as a `style=` attribute. That is what this does, as a markdown
tree-processor: the styles are applied while the document tree is being built,
which is exact, rather than by regexing the HTML afterwards, which is not.

Two things were wrong before, and only one of them was the styling:

* **The markdown was barely parsed.** With no extensions, a table arrived as a
  paragraph of literal pipes and a fenced code block became one inline `<code>`
  span with the language tag inside it. Agents write both constantly.
* **Nothing was styled**, so a mail client fell back to its own defaults: a
  serif face, text running the full width of a desktop window, and no spacing
  between anything.

The palette matches the display-resource cards in `email_render`, so a message
and the cards under it read as one email rather than two.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element

from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from markdown.util import HTML_PLACEHOLDER_RE

# Enabled deliberately, not by taste: agents emit tables and fenced code in
# almost every substantial answer, and `sane_lists` stops a numbered list
# restarting whenever a paragraph interrupts it.
EMAIL_MARKDOWN_EXTENSIONS = ("tables", "fenced_code", "sane_lists")

_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "'Helvetica Neue',Arial,sans-serif"
)
_MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

_INK = "#111827"
_MUTED = "#4b5563"
_LINE = "#d8dee4"
_WASH = "#f6f8fa"
_LINK = "#2563eb"

# One entry per tag python-markdown can emit. Anything absent is left alone
# rather than guessed at.
_STYLES: dict[str, str] = {
    "h1": f"color:{_INK};font-size:22px;font-weight:700;margin:0 0 12px;line-height:1.3;",
    "h2": f"color:{_INK};font-size:18px;font-weight:700;margin:24px 0 10px;line-height:1.35;",
    "h3": f"color:{_INK};font-size:16px;font-weight:600;margin:20px 0 8px;line-height:1.4;",
    "h4": f"color:{_INK};font-size:15px;font-weight:600;margin:18px 0 6px;",
    "h5": f"color:{_MUTED};font-size:14px;font-weight:600;margin:16px 0 6px;",
    "h6": f"color:{_MUTED};font-size:13px;font-weight:600;margin:16px 0 6px;",
    "p": "margin:0 0 14px;",
    "ul": "margin:0 0 14px;padding-left:22px;",
    "ol": "margin:0 0 14px;padding-left:22px;",
    "li": "margin:0 0 6px;",
    "a": f"color:{_LINK};text-decoration:underline;",
    "strong": f"color:{_INK};font-weight:600;",
    "em": "font-style:italic;",
    "blockquote": (
        f"border-left:3px solid {_LINE};color:{_MUTED};"
        "margin:0 0 14px;padding:2px 0 2px 14px;"
    ),
    "hr": f"border:0;border-top:1px solid {_LINE};margin:24px 0;",
    "table": (
        "border-collapse:collapse;margin:0 0 16px;width:100%;font-size:14px;"
    ),
    "th": (
        f"background:{_WASH};border:1px solid {_LINE};color:{_INK};"
        "font-weight:600;padding:8px 10px;text-align:left;"
    ),
    "td": f"border:1px solid {_LINE};padding:8px 10px;text-align:left;",
    "pre": (
        f"background:{_WASH};border:1px solid {_LINE};border-radius:6px;"
        f"font-family:{_MONO};font-size:13px;line-height:1.5;margin:0 0 16px;"
        "overflow-x:auto;padding:12px 14px;white-space:pre;"
    ),
    "code": (
        f"background:{_WASH};border-radius:4px;color:{_INK};"
        f"font-family:{_MONO};font-size:13px;padding:1px 5px;"
    ),
    "img": "max-width:100%;height:auto;",
}

# A `<code>` inside a `<pre>` must not carry the inline-span chrome, or every
# code block gets a second background and padding inside its own box.
_CODE_IN_PRE = f"background:transparent;font-family:{_MONO};font-size:13px;padding:0;"


def _is_stashed_block(element: Element) -> bool:
    """True for a `<p>` that is only a placeholder for stashed raw HTML.

    A fenced code block never reaches the tree: `fenced_code` stashes it during
    preprocessing and leaves a placeholder, which the block parser wraps in a
    paragraph. `RawHtmlPostprocessor` unwraps that again -- but it matches a
    *literal* `<p>PLACEHOLDER</p>`, so styling the paragraph stops the unwrap
    and the block ends up nested inside a `<p>`, which is invalid and loses the
    styling anyway. Leaving these alone is what keeps the unwrap working.
    """
    return (
        element.tag == "p"
        and len(element) == 0
        and bool(element.text)
        and HTML_PLACEHOLDER_RE.fullmatch(element.text.strip()) is not None
    )


class _InlineEmailStyles(Treeprocessor):
    """Stamps each element with the style its tag needs."""

    def run(self, root: Element) -> Element:
        for parent in root.iter():
            for child in parent:
                if _is_stashed_block(child):
                    continue
                style = (
                    _CODE_IN_PRE
                    if child.tag == "code" and parent.tag == "pre"
                    else _STYLES.get(child.tag)
                )
                if style and "style" not in child.attrib:
                    child.set("style", style)
        # `root` is the document wrapper; its own children were covered above.
        return root


class EmailStylesExtension(Extension):
    """Applies `_INLINE_STYLES` after the tree is built, before serialization."""

    def extendMarkdown(self, md) -> None:
        # After `inline` (so generated <a>/<code> exist) and after the table
        # extension's own processing; the priority just has to be low enough.
        md.treeprocessors.register(_InlineEmailStyles(md), "lemma_email_styles", 5)


def email_body_wrapper(inner_html: str) -> str:
    """Wrap rendered content in the container the tag styles assume.

    `max-width` is what stops a paragraph running the full width of a desktop
    window, which is most of why unstyled mail looks wrong; the rest is the
    font. Both have to live here because no client can be trusted to have a
    stylesheet.
    """
    return (
        f'<div style="color:{_INK};font-family:{_FONT};font-size:15px;'
        'line-height:1.6;max-width:640px;">'
        f"{inner_html}"
        "</div>"
    )


def style_stashed_code_blocks(html: str) -> str:
    """Style the `<pre>`/`<code>` a fenced block restores after serialization.

    These are the one thing the tree-processor cannot reach: the stash puts them
    back as raw text once the document is already a string. The substitution is
    narrow on purpose -- exactly the two opening tags `fenced_code` emits -- so
    it cannot touch a `<pre>` the agent wrote itself in an HTML reply.
    """
    return html.replace(
        "<pre><code", f'<pre style="{_STYLES["pre"]}"><code style="{_CODE_IN_PRE}"'
    )
