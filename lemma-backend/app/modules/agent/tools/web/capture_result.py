"""What a browser capture actually produced, as opposed to what was asked for.

Split out of `web_fetch` when that file went over the size ratchet, and the
seam is a real one: everything here answers one question -- given a capture
that has finished, what is on disk and what should the caller be told.

The rule it exists to enforce is that the answer comes from the filesystem.
Exit codes were trusted once, and a page the browser could not render came
back as a success naming a file that was never written.
"""

from __future__ import annotations

import shlex

from app.modules.agent.tools.web.blocked import classify
from app.modules.agent.tools.web.models import WebFetchPage

#: How much of the markdown to show back. Enough to recognise the page.
PREVIEW_CHARS = 400

#: Below this many characters of *body*, a capture rendered nothing worth
#: reading. Deliberately low, and matched to the cheap path's own floor:
#: plenty of real pages are short, and treating "short" as "broken" throws
#: away a page that was read correctly.
THIN_CONTENT_CHARS = 120


def expected_files(out_dir: str, name: str, formats: list[str]) -> dict[str, str]:
    suffix = {"markdown": "md", "pdf": "pdf", "jpeg": "jpg", "png": "png"}
    return {fmt: f"{out_dir}/{name}.{suffix[fmt]}" for fmt in formats if fmt in suffix}


def body_of(markdown: str) -> str:
    """The captured page, without the header the converter prepends.

    `webpage-to-markdown.mjs` writes a title line and a `Source:`/`Captured:`
    block before anything from the page, so a capture of an empty page is
    still a 71 byte file -- measured on `about:blank`. Checking the file size
    would therefore pass every empty render, and a flat floor on the file
    would depend on how long the URL is.
    """
    lines = markdown.splitlines()
    body = [line for line in lines[1:] if not line.startswith(("Source:", "Captured:"))]
    return "\n".join(body).strip()


async def present_files(session, paths: list[str]) -> dict[str, int]:
    """Of the captures we asked for, the ones that are really on disk.

    One command for the whole set, printing `size path` per non-empty file.
    """
    if not paths:
        return {}
    quoted = " ".join(shlex.quote(path) for path in paths)
    listing = await session.exec_command(
        cmd=(
            f'for f in {quoted}; do [ -s "$f" ] && '
            'printf "%s %s\\n" "$(wc -c < "$f" | tr -d " ")" "$f"; done'
        ),
        timeout=30,
    )
    sizes: dict[str, int] = {}
    for line in (listing.get("stdout") or "").splitlines():
        size, _, path = line.strip().partition(" ")
        if path and size.isdigit():
            sizes[path] = int(size)
    return sizes


async def finish(
    session,
    *,
    url: str,
    out_dir: str,
    name: str,
    formats: list[str],
    fetched_with: str,
    failure_output: str,
) -> WebFetchPage:
    """Report what the browser actually produced, not what was requested.

    The result used to be the *expected* paths plus `success=True` whenever the
    capture command did not exit non-zero — so a page the browser could not
    render (Britannica refuses ours) came back as a success naming a file that
    was never written, and the agent went looking for it. Exit codes were the
    wrong thing to trust anyway: a render outliving its wait window reports no
    exit code at all, which read as success. What is on disk is the answer.
    """
    expected = expected_files(out_dir, name, formats)
    present = await present_files(session, list(expected.values()))
    files = {fmt: path for fmt, path in expected.items() if path in present}

    markdown_path = files.get("markdown")
    if markdown_path is None:
        return WebFetchPage(
            url=url,
            success=False,
            fetched_with=fetched_with,
            error=(
                failure_output.strip()[:400]
                or "The browser produced no readable article for this page. "
                "Some sites refuse automated clients outright."
            ),
        )

    head = await session.exec_command(
        cmd=f"head -c {PREVIEW_CHARS * 2} {shlex.quote(markdown_path)}",
        timeout=20,
    )
    preview = None
    title = None
    text = (head.get("stdout") or "").strip()
    if text:
        title = text.splitlines()[0].lstrip("# ").strip() or None
        preview = text[:PREVIEW_CHARS]

    # The same question the http path asks, with the only evidence that
    # survives a render: the title. Rules 1-4 need response headers, and
    # markdown conversion keeps none -- so a Cloudflare interstitial the
    # browser rendered is caught here by its title and nothing else. Worth
    # the twelve lines anyway, because the failure without it is the http
    # path's exactly: "Verify you are human" clears the 120-character floor
    # and is saved as the article.
    rendered_block = classify(status=0, headers={}, body_snippet=text, title=title)
    if rendered_block.blocked:
        return WebFetchPage(
            url=url,
            success=False,
            title=title,
            fetched_with=fetched_with,
            blocked_by=rendered_block.vendor,
            error=(
                "The browser reached this page and was shown a bot check "
                "rather than the article. The page is not readable from this "
                "sandbox; use a different source."
            ),
        )

    # Thin text is reported, not treated as total failure -- and the files
    # that *were* produced are still handed back.
    #
    # The first version of this returned `success=False` with no `files` at
    # all, which threw away a perfectly good screenshot or PDF whenever the
    # page's text was short. A chart, a diagram, an image-led page and a
    # one-line status page are all legitimate captures; "few characters" is
    # a fact about the markdown, not a verdict on the request. It is only a
    # failure when markdown was the whole of what was asked for.
    thin = len(body_of(text)) < THIN_CONTENT_CHARS
    visual = [fmt for fmt in files if fmt != "markdown"]
    if thin and not visual:
        return WebFetchPage(
            url=url,
            success=False,
            title=title,
            fetched_with=fetched_with,
            error=(
                "The browser rendered this page but there was nothing to "
                "read in it. Either the site served an empty shell to an "
                "automated client, or what it shows needs an interaction "
                "first."
            ),
        )

    return WebFetchPage(
        notice=(
            "This page rendered with almost no text. The "
            f"{', '.join(sorted(visual))} capture is here and may be what you "
            "want; the markdown is nearly empty."
            if thin
            else None
        ),
        url=url,
        success=True,
        title=title,
        files=files,
        preview=preview,
        characters=present[markdown_path],
        fetched_with=fetched_with,
    )
