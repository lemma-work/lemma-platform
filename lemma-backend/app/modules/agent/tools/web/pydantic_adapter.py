from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.web.models import WebFetchRequest, WebFetchResponse
from app.modules.agent.tools.web.web import (
    web_search_internal,
)
from app.modules.agent.tools.web.web_fetch import web_fetch_internal
from app.core.log.log import get_logger
from app.core.web_search.web_search import WebSearchRequest, WebSearchResponse

logger = get_logger(__name__)


async def web_search(
    ctx: RunContext[BaseAgentContext], request: WebSearchRequest
) -> WebSearchResponse:
    """
    Search the web; returns titles, snippets, and URLs.

    Use it for current information, news, and finding online assets. Query with
    specific keywords rather than questions.
    """
    try:
        return await web_search_internal(ctx.deps, request)
    except Exception as e:
        logger.debug("agent.web_search.failed", exc_info=True)
        return WebSearchResponse(
            success=False,
            error=f"Web search failed: {e}",
        )


async def web_fetch(
    ctx: RunContext[BaseAgentContext], request: WebFetchRequest
) -> WebFetchResponse:
    """
    Capture web pages into your workspace so you can read them properly.

    Takes a *list* of URLs — fetch everything a question needs in one call.
    Each page is saved as readable markdown (optionally also PDF and a
    full-page screenshot) under `out_dir`, and you get back the file paths and
    a short preview, not the page text: twenty articles inline would bury the
    task you are working on.

    Then work with the files: `exec_command` to grep or read them,
    `view_image` for a screenshot, `pod_view_document_pages` for a captured
    PDF. This is the right way to read anything longer than a search snippet.
    """
    try:
        return await web_fetch_internal(ctx.deps, request)
    except Exception as exc:
        logger.debug("agent.web_fetch.failed", exc_info=True)
        return WebFetchResponse(success=False, error=f"Web fetch failed: {exc}")


web_search_toolset = FunctionToolset[BaseAgentContext](
    tools=[
        web_search,
        web_fetch,
    ]
)
