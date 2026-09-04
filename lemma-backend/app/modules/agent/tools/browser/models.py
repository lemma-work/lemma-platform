from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.modules.agent.tools.context import TOOL_COMMENT_DESC, BaseToolResponse

# Interactive-only snapshots are the working default: the full accessibility
# tree of a real page is thousands of nodes, and every one of them costs the
# agent context it could have spent on the task.
DEFAULT_SNAPSHOT_MAX_TOKENS = 4000


class BrowserOpenRequest(BaseModel):
    url: str = Field(
        description=(
            "Absolute URL to open, e.g. `https://example.com`. For an app "
            "running inside this sandbox use `http://127.0.0.1:<port>`, never a "
            "public preview URL."
        )
    )
    wait_for_url: Optional[str] = Field(
        default=None,
        description=(
            "Glob to wait for after loading, e.g. `**/dashboard`. Use it when "
            "opening triggers a redirect you need to land on before reading."
        ),
    )
    wait_for_text: Optional[str] = Field(
        default=None,
        description="Text to wait for on the page before returning.",
    )
    max_snapshot_tokens: int = Field(
        default=DEFAULT_SNAPSHOT_MAX_TOKENS,
        ge=200,
        le=40000,
        description="Truncate the returned snapshot past this many tokens.",
    )
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class BrowserSnapshotRequest(BaseModel):
    interactive_only: bool = Field(
        default=True,
        description=(
            "Only elements you can act on. Turn it off only when you need page "
            "text you cannot get with `browser_read`, and expect a large result."
        ),
    )
    max_snapshot_tokens: int = Field(
        default=DEFAULT_SNAPSHOT_MAX_TOKENS,
        ge=200,
        le=40000,
        description="Truncate the returned snapshot past this many tokens.",
    )
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class BrowserActRequest(BaseModel):
    """Act on one element, then return the page as it now is."""

    action: Literal[
        "click",
        "fill",
        "type",
        "press",
        "select",
        "check",
        "uncheck",
        "hover",
        "scroll",
    ] = Field(description="What to do.")
    target: Optional[str] = Field(
        default=None,
        description=(
            "The element, as an `@eN` ref from the most recent snapshot. A CSS "
            "selector works too but is a last resort. Not needed for `press` "
            "(which goes to the focused element) or `scroll`."
        ),
    )
    text: Optional[str] = Field(
        default=None,
        description=(
            "Text for `fill` (clears first) and `type` (appends), or the option "
            "label for `select`."
        ),
    )
    key: Optional[str] = Field(
        default=None,
        description="Key for `press`, e.g. `Enter`, `Escape`, `Tab`.",
    )
    scroll_direction: Literal["up", "down", "left", "right"] = Field(
        default="down",
        description="Direction for `scroll`.",
    )
    scroll_amount: int = Field(
        default=500,
        ge=1,
        le=20000,
        description="Pixels to scroll.",
    )
    wait_for_url: Optional[str] = Field(
        default=None,
        description=(
            "Glob to wait for after acting, e.g. `**/dashboard`. Use it "
            "whenever the action navigates or submits."
        ),
    )
    wait_for_text: Optional[str] = Field(
        default=None,
        description="Text to wait for after acting, e.g. a success message.",
    )
    max_snapshot_tokens: int = Field(
        default=DEFAULT_SNAPSHOT_MAX_TOKENS,
        ge=200,
        le=40000,
        description="Truncate the returned snapshot past this many tokens.",
    )
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class BrowserReadRequest(BaseModel):
    what: Literal["text", "html", "url", "title", "attr", "console", "network"] = Field(
        description="What to read from the page."
    )
    target: Optional[str] = Field(
        default=None,
        description=(
            "Element `@eN` ref for `text` and `attr`. Omit for `html` to get the "
            "whole document. Ignored by `url`, `title`, `console` and `network`."
        ),
    )
    attribute: Optional[str] = Field(
        default=None,
        description="Attribute name for `attr`, e.g. `href`.",
    )
    max_output_tokens: int = Field(
        default=4000,
        ge=200,
        le=200000,
        description="Truncate the result past this many tokens.",
    )
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class BrowserScreenshotRequest(BaseModel):
    full_page: bool = Field(
        default=False,
        description="Capture the whole scroll height rather than the viewport.",
    )
    annotate: bool = Field(
        default=False,
        description=(
            "Draw numbered labels on interactive elements, keyed to the `@eN` "
            "refs a snapshot returns. Use it when you need to see which element "
            "is which."
        ),
    )
    instructions: Optional[str] = Field(
        default=None,
        description=(
            "What you need from the screenshot — 'is the chart rendered', "
            "'read the error banner', 'does the layout break'. Always set it: "
            "if this agent's model cannot see images, this is the question a "
            "vision model answers on your behalf, and a vague question gets a "
            "vague answer."
        ),
    )
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class BrowserResult(BaseToolResponse):
    url: Optional[str] = Field(
        default=None, description="The page's URL after the call."
    )
    title: Optional[str] = Field(
        default=None, description="The page's title after the call."
    )
    snapshot: Optional[str] = Field(
        default=None,
        description=(
            "The page's elements with their `@eN` refs. Refs go stale the "
            "moment the page changes, so use the ones from this result rather "
            "than any from an earlier call."
        ),
    )
    output: Optional[str] = Field(
        default=None,
        description="What the command returned, for reads and diagnostics.",
    )
    truncated: bool = Field(
        default=False,
        description="Whether the snapshot or output was cut short by the limit.",
    )


class BrowserScreenshotResponse(BaseToolResponse):
    url: Optional[str] = Field(
        default=None, description="The page the screenshot was taken of."
    )
    title: Optional[str] = Field(default=None, description="The page's title.")
    media_type: Optional[str] = Field(
        default=None, description="MIME type of the returned image."
    )
    size_bytes: Optional[int] = Field(
        default=None, description="Size of the captured image in bytes."
    )
    full_page: bool = Field(
        default=False, description="Whether the whole scroll height was captured."
    )
