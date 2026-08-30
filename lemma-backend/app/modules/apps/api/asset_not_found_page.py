"""What a person sees when an app has no file at the path they clicked.

The route answers two audiences with one URL. A script fetching a missing
bundle asset wants the JSON error it has always had; a person who clicked a
link wants to know what happened and where to go instead. `Accept` is what
separates them, so only a navigation is given a page.

The case that produced this: an app rendering markdown whose links point at pod
files -- `library/rust/ownership.md` -- which a browser resolves against the
app's own origin. The app does not serve it, so the click ended on a raw JSON
body in a window with no way back, which reads as the app being broken.

Nothing here looks the file up. The route is public and unauthenticated, and a
lookup that answered "yes, that exists" would let anyone enumerate a pod's file
paths through a published app. The offer is made on the shape of the path
alone; the workspace holds the session, so it is the right place to find out
whether the file is really there and whether this reader may see it.
"""

from __future__ import annotations

from html import escape
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse
from uuid import UUID

from app.core.config import settings

# Extensions the workspace can open as a document. A missing `.js` or `.css` is
# a broken build and gets no offer -- suggesting the workspace for a chunk file
# would be noise pointed at a person who cannot act on it.
_DOCUMENT_SUFFIXES = frozenset(
    {
        ".md",
        ".markdown",
        ".mdx",
        ".txt",
        ".csv",
        ".json",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
    }
)


def looks_like_a_document(asset_path: str) -> bool:
    """Whether a missing path is worth offering to open in the workspace."""
    return PurePosixPath(asset_path).suffix.lower() in _DOCUMENT_SUFFIXES


def workspace_file_url(pod_id: UUID, asset_path: str) -> str | None:
    """Where the workspace shows one pod file, or None if it cannot be built.

    `?file=` is the document view's own deep link, the same one the file list
    navigates to, so this adds no route that has to be kept working separately.
    """
    base = (settings.frontend_url or "").rstrip("/")
    if not base or urlparse(base).scheme not in {"http", "https"}:
        return None
    return f"{base}/pod/{pod_id}/files?file={quote(asset_path, safe='')}"


def render_asset_not_found_page(
    *,
    asset_path: str,
    pod_id: UUID | None,
    app_name: str | None = None,
) -> str:
    """A readable dead end, with the ways out this request actually has."""
    where = escape(asset_path)
    app = escape(app_name) if app_name else "this app"
    workspace_url = (
        workspace_file_url(pod_id, asset_path)
        if pod_id is not None and looks_like_a_document(asset_path)
        else None
    )

    # `_top` matters: an app is often running inside the workspace's own frame,
    # and a link that replaced only the frame would leave the reader looking at
    # the workspace nested inside itself.
    offer = (
        f'<a class="primary" target="_top" href="{escape(workspace_url)}">'
        "Open it in your workspace</a>"
        if workspace_url
        else ""
    )
    explanation = (
        "Links to files live in your workspace, not in the app bundle."
        if workspace_url
        else "Check the link, or go back to the app's home page."
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not part of this app</title>
<style>
  :root {{
    color-scheme: light dark;
    --ground: #ffffff;
    --ink: #16181d;
    --muted: #5c6270;
    --line: #e3e5ea;
    --accent: #2f5bd8;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --ground: #14161a;
      --ink: #f2f3f5;
      --muted: #9aa1af;
      --line: #2a2e36;
      --accent: #8ea9f5;
    }}
  }}
  html, body {{ height: 100%; }}
  body {{
    margin: 0;
    display: grid;
    place-items: center;
    padding: 2rem 1.5rem;
    background: var(--ground);
    color: var(--ink);
    font: 15px/1.6 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  }}
  main {{ max-width: 34rem; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 0.6rem; letter-spacing: -0.01em; }}
  p {{ margin: 0 0 1rem; color: var(--muted); }}
  code {{
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.9em;
    padding: 0.15em 0.4em;
    border: 1px solid var(--line);
    border-radius: 5px;
    word-break: break-all;
    color: var(--ink);
  }}
  .actions {{ display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 1.4rem; }}
  a {{
    display: inline-block;
    padding: 0.5rem 0.9rem;
    border: 1px solid var(--line);
    border-radius: 7px;
    color: var(--ink);
    text-decoration: none;
  }}
  a.primary {{ border-color: var(--accent); color: var(--accent); }}
  a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
</style>
</head>
<body>
<main>
  <h1>Not part of this app</h1>
  <p>{app} has no file at <code>{where}</code>. {explanation}</p>
  <div class="actions">
    {offer}
    <a href="/">Back to the app</a>
  </div>
</main>
</body>
</html>
"""
