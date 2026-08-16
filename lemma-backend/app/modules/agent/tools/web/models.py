"""Request/response shapes for `web_fetch`."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

WebFetchFormat = Literal["markdown", "pdf", "jpeg", "png"]


class WebFetchRequest(BaseModel):
    urls: list[str] = Field(
        ...,
        min_length=1,
        # Must equal `_MAX_BROWSER_RENDERS`, and `test_web_fetch_limits_agree`
        # holds the two together. Advertising ten while rendering three meant a
        # caller who sent ten JS-heavy pages was told, only after paying for
        # the call, that seven of them were skipped -- a limit the schema had
        # no way to express and the agent had no way to plan around. Accepting
        # exactly what can be delivered is what makes the cap honest.
        max_length=5,
        description=(
            "Pages to capture, in one call — up to 5. Batching is the point: "
            "research usually means reading several sources, not one. Static "
            "pages are fetched in parallel and are quick; pages that need the "
            "full browser render one at a time, so a call is capped at a few "
            "minutes and reports anything it did not reach. If some come back "
            "'not attempted', ask for those again rather than repeating the "
            "whole list."
        ),
    )
    formats: list[WebFetchFormat] = Field(
        default_factory=lambda: ["markdown"],
        description=(
            "What to save per page. `markdown` is the readable article text "
            "and is what you normally want; `pdf` and `jpeg`/`png` preserve "
            "the rendered layout — useful when the page is a chart, a table, "
            "or a design you need to *look* at with `view_image`."
        ),
    )
    out_dir: str = Field(
        default="research",
        description=(
            "Directory in the workspace to write into, relative to your "
            "working directory. Files land here; nothing large is returned "
            "inline."
        ),
    )
    render: bool = Field(
        default=False,
        description=(
            "Force the full browser. Off by default because a plain fetch is "
            "seconds faster; turn it on for pages that build their content "
            "with JavaScript, or when the plain fetch came back near-empty. "
            "Always used for `pdf`/`jpeg`/`png`, which need a real render."
        ),
    )
    comment: Optional[str] = Field(
        default=None,
        description="One line on why you are fetching these, for the activity log.",
    )


class WebFetchPage(BaseModel):
    url: str
    success: bool
    title: Optional[str] = None
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Saved workspace paths, keyed by format.",
    )
    preview: Optional[str] = Field(
        default=None,
        description=(
            "First few hundred characters of the extracted text — enough to "
            "tell whether the page is worth reading in full."
        ),
    )
    characters: Optional[int] = Field(
        default=None, description="Length of the extracted markdown."
    )
    fetched_with: Optional[Literal["http", "browser"]] = None
    error: Optional[str] = None


class WebFetchResponse(BaseModel):
    success: bool
    out_dir: Optional[str] = None
    pages: list[WebFetchPage] = Field(default_factory=list)
    message: Optional[str] = None
    error: Optional[str] = None
