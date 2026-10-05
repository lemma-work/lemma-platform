"""What a shared page puts on screen from elsewhere in the pod.

A public link opens one file. A page is rarely one file: a markdown report
shows the chart beside it, an HTML report pulls in its stylesheet and its
script. Those travel with the link — but only those, and only while the page
still asks for them. Nothing is recorded at mint time; every fetch asks the
page as it is now, which is what keeps a shared page live rather than a
snapshot of the moment somebody pressed Share.

Embedded, not linked. ``![chart](chart.png)`` and ``<img src>`` are part of
reading the page; ``[the plan](plan.md)`` and ``<a href>`` are a way to leave it
for another document, and that document was not what was shared.

Pure on purpose: the public route decides *whether* to serve from this, so it
has to be testable without a pod, a store or a request.
"""

from __future__ import annotations

import html
import posixpath
import re
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import unquote

from app.core.authorization.context import ResourceVisibility

PageKind = Literal["markdown", "html"]

#: A page bigger than this is not scanned, and so carries nothing with it. A
#: report is kilobytes; this only bounds what an anonymous fetch can make the
#: server parse.
MAX_SCANNED_PAGE_BYTES = 2 * 1024 * 1024

_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+[\"'(][^)]*)?\)")
_MARKDOWN_IMAGE_DEFINITION = re.compile(r"!\[[^\]]*\]\[([^\]]*)\]")
_REFERENCE_DEFINITION = re.compile(
    r"^\s{0,3}\[([^\]]+)\]:\s*(<[^>]*>|\S+)", re.MULTILINE
)
_CSS_URL = re.compile(
    r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^)\s]*))\s*\)", re.IGNORECASE
)
_CSS_IMPORT = re.compile(r"@import\s+(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)

#: The attributes that load something into the page, per element. `href` only
#: counts on a `<link>` that is a stylesheet or an icon — see ``_LINK_RELS``.
_EMBEDDING_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "track": ("src",),
    "script": ("src",),
    "link": ("href",),
}
_LINK_RELS = {"stylesheet", "icon", "apple-touch-icon"}


def normalize_reference(reference: str) -> str | None:
    """The reference as the page means it, or None when it is not the pod's.

    Shared by the scan and the request so the two agree on what a reference is:
    entities decoded, angle brackets and whitespace dropped, query and fragment
    cut, percent-escapes undone (the page editor writes spaces as ``%20``).
    A URL with a scheme, a protocol-relative URL, a data URI and a bare
    fragment are somebody else's, and are left to the browser.
    """
    value = html.unescape(reference).strip().strip("<>").strip()
    if not value or value.startswith(("#", "//")) or _SCHEME.match(value):
        return None
    value = value.split("#", 1)[0].split("?", 1)[0]
    value = unquote(value).strip()
    return value or None


def resolve_reference(reference: str, *, page_path: str) -> str | None:
    """The pod path a normalized reference names, read from ``page_path``.

    Absolute references are the pod's own paths, ``/me/…`` included — that
    alias is resolved later, against the person who shared the page. Relative
    ones are read from the page's folder. One that climbs out of the pod root
    names nothing.
    """
    if reference.startswith("/"):
        joined = reference
    else:
        folder = posixpath.dirname(page_path) or "/"
        joined = posixpath.join(folder, reference)
    depth = 0
    for segment in joined.split("/"):
        if segment == "..":
            depth -= 1
            if depth < 0:
                return None
        elif segment and segment != ".":
            depth += 1
    resolved = posixpath.normpath(joined)
    if resolved in {"", "/", "."}:
        return None
    return "/" + resolved.lstrip("/")


def _srcset_urls(value: str) -> list[str]:
    """Each candidate URL in a `srcset`, without its width or density."""
    return [part.strip().split()[0] for part in value.split(",") if part.strip()]


def _css_references(css: str) -> list[str]:
    found: list[str] = []
    for match in _CSS_URL.finditer(css):
        found.append(next(group for group in match.groups() if group is not None))
    for match in _CSS_IMPORT.finditer(css):
        found.append(next(group for group in match.groups() if group is not None))
    return found


class _EmbeddedResourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        style = values.get("style")
        if style:
            self.found.extend(_css_references(style))
        if tag == "style":
            self._in_style = True
            return
        wanted = _EMBEDDING_ATTRIBUTES.get(tag)
        if not wanted:
            return
        if tag == "link":
            rels = set(values.get("rel", "").lower().split())
            if not rels & _LINK_RELS:
                return
        for attribute in wanted:
            value = values.get(attribute)
            if not value:
                continue
            if attribute == "srcset":
                self.found.extend(_srcset_urls(value))
            else:
                self.found.append(value)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.found.extend(_css_references(data))


def _html_references(text: str) -> list[str]:
    parser = _EmbeddedResourceParser()
    parser.feed(text)
    parser.close()
    return parser.found


def _markdown_references(text: str) -> list[str]:
    found = [match.group(1) for match in _MARKDOWN_IMAGE.finditer(text)]
    definitions = {
        label.strip().lower(): target
        for label, target in _REFERENCE_DEFINITION.findall(text)
    }
    for match in _MARKDOWN_IMAGE_DEFINITION.finditer(text):
        target = definitions.get(match.group(1).strip().lower())
        if target:
            found.append(target)
    # Markdown carries raw HTML too, and an agent writing `<img src>` into a
    # report means the same thing as `![]()`.
    found.extend(_html_references(text))
    return found


def embedded_references(text: str, *, kind: PageKind) -> frozenset[str]:
    """Every normalized reference the page loads from the pod, as written."""
    raw = _markdown_references(text) if kind == "markdown" else _html_references(text)
    normalized = (normalize_reference(reference) for reference in raw)
    return frozenset(reference for reference in normalized if reference)


def may_travel_with(*, page_visibility: str, resource_visibility: str) -> bool:
    """Whether a page may carry this resource out through a public link.

    A personal page is written only by its owner, who could share anything it
    shows directly, so it carries whatever they can read. Any other page is
    written by more people than shared it — a pod page by everyone in the pod —
    and the link is checked as the person who shared it. Letting such a page
    carry a personal or restricted file would let anyone who can edit the page
    publish a file the sharer can read and they cannot, by writing its path in.
    So a shared page carries only what everybody in the pod could already open.
    """
    if page_visibility == ResourceVisibility.PERSONAL.value:
        return True
    return resource_visibility in {
        ResourceVisibility.POD.value,
        ResourceVisibility.PUBLIC.value,
    }


def page_kind(content_type: str, filename: str) -> PageKind | None:
    """Whether a shared file is a page that can carry anything, and which kind.

    The extension breaks ties the type leaves, for the same reason the reader's
    page does: markdown arrives as `text/plain` often enough.
    """
    base = content_type.split(";", 1)[0].strip().lower()
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if base == "text/markdown" or extension in {"md", "markdown"}:
        return "markdown"
    if base == "text/html" or extension in {"html", "htm"}:
        return "html"
    return None
