
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.web.web import (
    web_search_internal,
)
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


web_search_toolset = FunctionToolset[BaseAgentContext](
    tools=[
        web_search,
    ]
)
