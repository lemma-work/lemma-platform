from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.web.models import (
    WebFetchRequest,
    WebFetchResponse,
)
from app.modules.agent.infrastructure.tools.web.web_fetch import web_fetch_internal
from app.core.log.log import get_logger
from app.core.web_search.web_search import (
    WebSearchRequest,
    WebSearchResponse,
    search_web,
)

logger = get_logger(__name__)


async def web_search(
    ctx: RunContext[ToolContext], request: WebSearchRequest
) -> WebSearchResponse:
    """
    Search the web; returns titles, snippets, and URLs.

    Use it for current information, news, and finding online assets. Query with
    specific keywords rather than questions.
    """
    # No handler here: `search_web` turns every failure into a result with its
    # reason, so one wrapped around it only ever caught what it already had.
    return await search_web(request)


async def web_fetch(
    ctx: RunContext[ToolContext], request: WebFetchRequest
) -> WebFetchResponse:
    """
    Capture web pages into your workspace so you can read them properly.

    Takes a *list* of URLs — fetch everything a question needs in one call.
    Each page is saved as readable markdown (optionally also PDF and a
    full-page screenshot) under `out_dir`, and you get back the file paths and
    a short preview, not the page text: twenty articles inline would bury the
    task you are working on.

    Then work with the files: `exec_command` to grep or read them, and
    `view_image` for a screenshot. Both take the workspace path you were given.
    `pod_view_document_pages` takes a path *in the pod*, so it only works on a
    captured PDF after you have uploaded it. If you have no `exec_command`,
    each page's text comes back in its `content` instead, clipped to a budget.
    JSON and plain-text URLs are saved as they came. This is the right way to
    read anything longer than a search snippet.
    """
    try:
        return await web_fetch_internal(ctx.deps, request)
    except Exception as exc:
        logger.warning("agent.web_fetch.failed.degraded", exc_info=True)
        return WebFetchResponse(success=False, error=f"Web fetch failed: {exc}")


web_search_toolset = FunctionToolset[ToolContext](
    tools=[
        web_search,
        web_fetch,
    ]
)
